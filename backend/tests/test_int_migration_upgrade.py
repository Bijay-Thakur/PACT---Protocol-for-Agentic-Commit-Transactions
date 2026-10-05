"""A44: clean install and upgrade from populated Phase 1 data preserve evidence;
incomplete legacy metadata cannot auto-execute. A45: lossy downgrade refuses by default."""

from __future__ import annotations

import asyncio
import datetime
import decimal
import json
import os
import uuid
from pathlib import Path

import asyncpg
import pytest

from app.domain.receipt import receipt_digest

from .conftest import BACKEND, TEST_DB, _in_thread, requires_db
from .db_guard import check_and_mark, check_static, write_marker

pytestmark = requires_db
FIXTURE = Path(__file__).with_name("fixtures") / "phase1_db.json"
ORDER = ["transactions", "capabilities", "logical_operations", "effects", "effect_dependencies", "invariants",
         "invariant_evaluations", "operation_attempts", "transaction_events", "commit_decisions",
         "operator_actions", "receipts"]


def _dec(v):
    if isinstance(v, dict) and "__dt__" in v:
        return datetime.datetime.fromisoformat(v["__dt__"])
    if isinstance(v, dict) and "__dec__" in v:
        return decimal.Decimal(v["__dec__"])
    return v


def _alembic(url: str, target: str, downgrade: bool = False) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.attributes["url"] = url
    (command.downgrade if downgrade else command.upgrade)(cfg, target)


@pytest.fixture(scope="module")
def upgrade_db() -> str:
    base = TEST_DB.rsplit("/", 1)[0]
    url = f"{base}/pact_upgrade_test"
    check_static(url)

    async def recreate():
        try:
            existing = await asyncpg.connect(url.replace("+asyncpg", ""))
        except asyncpg.InvalidCatalogNameError:
            existing = None
        if existing is not None:
            try:
                await check_and_mark(existing, url)
            finally:
                await existing.close()
        admin = await asyncpg.connect(base.replace("+asyncpg", "") + "/postgres")
        await admin.execute("DROP DATABASE IF EXISTS pact_upgrade_test")
        await admin.execute("CREATE DATABASE pact_upgrade_test")
        await admin.close()
        created = await asyncpg.connect(url.replace("+asyncpg", ""))
        try:
            await write_marker(created)
        finally:
            await created.close()

    _in_thread(lambda: asyncio.run(recreate()))
    return url


async def _load_fixture(url: str) -> dict[str, int]:
    data = json.loads(FIXTURE.read_text())["tables"]
    conn = await asyncpg.connect(url.replace("+asyncpg", ""))
    cols = {}
    try:
        for table in ORDER:
            types = {r["column_name"]: r["data_type"] for r in await conn.fetch(
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_name=$1", table)}
            cols[table] = len(data[table])
            rows = data[table]
            if table == "transactions":  # parents before children (self-referencing FK)
                rows = sorted(rows, key=lambda r: r["depth"])
            if table == "capabilities":
                rows = sorted(rows, key=lambda r: r["parent_capability_id"] is not None)
            for row in rows:
                keys = list(row)
                vals = []
                for k in keys:
                    v = _dec(row[k])
                    if types[k] == "jsonb" and not isinstance(v, str):  # asyncpg exported JSONB as text
                        v = json.dumps(v)
                    elif types[k] == "uuid" and v is not None:
                        v = uuid.UUID(v)
                    vals.append(v)
                placeholders = ", ".join(f"${i + 1}" for i in range(len(keys)))
                if table == "transaction_events":  # identity column: keep original sequence numbers
                    await conn.execute(f"INSERT INTO {table} ({', '.join(keys)}) OVERRIDING SYSTEM VALUE "
                                       f"VALUES ({placeholders})", *vals)
                else:
                    await conn.execute(f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({placeholders})", *vals)
    finally:
        await conn.close()
    return cols


async def test_upgrade_from_populated_phase1_preserves_evidence(upgrade_db):
    _in_thread(lambda: _alembic(upgrade_db, "0001"))
    counts = await _load_fixture(upgrade_db)
    _in_thread(lambda: _alembic(upgrade_db, "head"))

    conn = await asyncpg.connect(upgrade_db.replace("+asyncpg", ""))
    try:
        for table, n in counts.items():
            assert await conn.fetchval(f"SELECT count(*) FROM {table}") == n, table
        # Old receipts remain byte-for-byte verifiable under their original schema (A31).
        for r in await conn.fetch("SELECT payload, sha256, receipt_version FROM receipts"):
            payload = json.loads(r["payload"])
            assert r["receipt_version"] == "pact.receipt/v1"
            assert receipt_digest(payload) == r["sha256"] == payload["receipt_hash"]

        roots = {r["state"]: r for r in await conn.fetch(
            "SELECT id, state, quarantined, quarantine_reason FROM transactions WHERE parent_id IS NULL")}
        for state in ("UNKNOWN", "HUMAN_REQUIRED"):
            assert roots[state]["quarantined"] is True, state  # cannot auto-execute
            assert "Phase 1" in roots[state]["quarantine_reason"]
        for state in ("COMMITTED_VERIFIED", "ABORTED", "COMPENSATED"):
            assert roots[state]["quarantined"] is False

        # The in-flight legacy refund is UNKNOWN - never backfilled as "not applied".
        unknown_refund = await conn.fetchrow(
            "SELECT application, postcondition, legacy FROM effects WHERE root_id=$1 AND contract_type='billing.refund'",
            roots["UNKNOWN"]["id"])
        assert dict(unknown_refund) == {"application": "UNKNOWN", "postcondition": "UNDETERMINED", "legacy": True}
        # The failed compensation's original effect is applied with an unknown restoration.
        sub = await conn.fetchrow(
            "SELECT application, restoration FROM effects WHERE root_id=$1 AND contract_type='subscription.cancel'",
            roots["HUMAN_REQUIRED"]["id"])
        assert dict(sub) == {"application": "APPLIED", "restoration": "UNKNOWN"}
        # Unresolved legacy consequences are surfaced as residual obligations.
        kinds = {r["kind"] for r in await conn.fetch(
            "SELECT kind FROM residual_obligations WHERE root_id = ANY($1::uuid[])",
            [roots["UNKNOWN"]["id"], roots["HUMAN_REQUIRED"]["id"]])}
        assert kinds == {"UNKNOWN_OUTCOME", "COMPENSATION_UNKNOWN"}
        # No approvals were synthesized and no work was enqueued for legacy rows.
        assert await conn.fetchval("SELECT count(*) FROM approvals") == 0
        assert await conn.fetchval("SELECT count(*) FROM work_items") == 0
        assert await conn.fetchval("SELECT count(*) FROM tenants WHERE id='demo'") == 1
    finally:
        await conn.close()


async def test_lossy_downgrade_refuses_without_explicit_opt_in(upgrade_db, monkeypatch):
    monkeypatch.delenv("PACT_ALLOW_LOSSY_DOWNGRADE", raising=False)
    with pytest.raises(RuntimeError, match="PACT_ALLOW_LOSSY_DOWNGRADE"):
        _in_thread(lambda: _alembic(upgrade_db, "0001", downgrade=True))
    assert os.environ.get("PACT_ALLOW_LOSSY_DOWNGRADE") is None
