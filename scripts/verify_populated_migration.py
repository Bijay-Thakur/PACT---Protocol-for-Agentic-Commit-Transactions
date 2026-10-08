"""Copy the guarded disposable test DB and verify 0002 -> current-head preservation.

Never opens or migrates the working PACT database. The probe database is a
private copy of pact_phase21_test and is removed only if this process created it.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_disposable_tests import working_url  # noqa: E402
from backend.tests.db_guard import check_static  # noqa: E402

SOURCE_NAME = "pact_phase21_test"
PROBE_NAME = "pact_phase21_migration_test"


def with_db(url: str, name: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{name}", parsed.query, parsed.fragment))


async def snapshot(url: str) -> dict:
    conn = await asyncpg.connect(url.replace("+asyncpg", ""))
    try:
        result = {}
        for table in ("transactions", "effects", "operation_attempts", "observations",
                      "receipts", "residual_obligations", "logical_operations"):
            result[table] = await conn.fetchval(f"SELECT count(*) FROM {table}")
        result["unknown"] = await conn.fetchval("SELECT count(*) FROM transactions WHERE state='UNKNOWN'")
        result["inflight"] = await conn.fetchval(
            "SELECT count(*) FROM effects WHERE state IN ('DISPATCHING','DISPATCHED','VERIFYING')")
        result["receipt_payloads"] = sorted((str(r["id"]), r["payload"])
            for r in await conn.fetch("SELECT id, payload::text AS payload FROM receipts"))
        result["observation_digests"] = sorted((str(r["id"]), r["evidence_digest"])
            for r in await conn.fetch("SELECT id, evidence_digest FROM observations"))
        return result
    finally:
        await conn.close()


async def main() -> None:
    work = working_url()
    source = with_db(work, SOURCE_NAME)
    probe = with_db(work, PROBE_NAME)
    check_static(source, [work])
    check_static(probe, [work, source])
    admin = await asyncpg.connect(with_db(work, "postgres").replace("+asyncpg", ""))
    created = False
    try:
        if await admin.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", PROBE_NAME):
            raise RuntimeError("migration probe already exists; refusing to use or drop it")
        await admin.execute(f'CREATE DATABASE "{PROBE_NAME}" TEMPLATE "{SOURCE_NAME}"')
        created = True
        # Mark copied rows as unresolved so the upgrade is checked against
        # populated UNKNOWN and in-flight states as well as terminal receipts.
        seed = await asyncpg.connect(probe.replace("+asyncpg", ""))
        try:
            await seed.execute("UPDATE transactions SET state='UNKNOWN' WHERE id=(SELECT id FROM transactions LIMIT 1)")
            await seed.execute("UPDATE effects SET state='DISPATCHING' WHERE id=(SELECT id FROM effects LIMIT 1)")
        finally:
            await seed.close()
        before = await snapshot(probe)
        if any(before[name] < 1 for name in ("receipts", "observations", "residual_obligations",
                                                "unknown", "inflight")):
            raise RuntimeError("test DB is not populated with required Phase 2 obligations")
        env = {**os.environ, "PACT_DATABASE_URL": probe}
        for command in (("downgrade", "0002"), ("upgrade", "head")):
            subprocess.run([sys.executable, "-m", "alembic", "-c", "alembic.ini", *command],
                           cwd=ROOT / "backend", env=env, check=True, capture_output=True)
        after = await snapshot(probe)
        if before != after:
            raise AssertionError("populated migration changed protected counts or evidence")
        print({"status": "PASS", "migration": "0002 -> 0004", "rows": {
            name: before[name] for name in ("receipts", "observations", "effects", "operation_attempts",
                                           "residual_obligations", "unknown", "inflight")}})
    finally:
        if created:
            await admin.execute(f'DROP DATABASE "{PROBE_NAME}" WITH (FORCE)')
        await admin.close()


if __name__ == "__main__":
    asyncio.run(main())
