"""PACT local administration and simulator commands (run from backend/).

    python -m app.cli migrate
    python -m app.cli create-operator TENANT USERNAME
    python -m app.cli create-agent TENANT NAME GRANTS_JSON_FILE
    python -m app.cli scenario [name ...]
    python -m app.cli crash-midflight
    python -m app.cli recover
    python -m app.cli planner-smoke "Cancel customer C-123"
"""

from __future__ import annotations

import asyncio
import getpass
import json
import os
import sys
import uuid
from dataclasses import replace
from pathlib import Path
from uuid import UUID

from app.config import Settings


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    backend = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "migrations"))
    command.upgrade(cfg, "head")


async def _provision(kind: str, args: list[str]) -> int:
    from app.domain.enums import PrincipalKind
    from app.runtime import Runtime

    rt = Runtime(Settings())
    try:
        if kind == "create-operator" and len(args) == 2:
            tenant, username = args
            password = getpass.getpass("Operator password: ")
            if password != getpass.getpass("Confirm password: ") or len(password) < 12:
                raise ValueError("passwords must match and be at least 12 characters")
            p = await rt.principals.upsert_principal(tenant, username, PrincipalKind.OPERATOR,
                ["op:approve", "op:recover", "op:attest", "tx:read_all", "demo:run",
                 "planner:propose"],
                {"roles": ["refund_approver", "finance_approver", "code_approver"]})
            await rt.principals.set_password(p.id, username, password)
            print(json.dumps({"principal": username, "tenant": tenant, "kind": "OPERATOR"}))
            return 0
        if kind == "create-agent" and len(args) == 3:
            tenant, name, path = args
            grants = json.loads(Path(path).read_text(encoding="utf-8"))
            p = await rt.principals.upsert_principal(tenant, name, PrincipalKind.AGENT,
                ["tx:begin", "tx:delegate", "tx:propose", "tx:prepare", "tx:commit", "tx:abort"], grants)
            key = await rt.principals.issue_api_key(p.id, label="local CLI provision")
            print(json.dumps({"principal": name, "tenant": tenant, "api_key_once": key}))
            return 0
        print(__doc__)
        return 2
    finally:
        await rt.stop()


async def _scenarios(names: list[str]) -> int:
    from app.api.demo import RunRequest, run
    from app.demo.scenarios import SCENARIOS
    from app.domain.enums import PrincipalKind
    from app.runtime import Runtime

    settings = replace(Settings(), demo_mode=True, embedded_worker=False, auto_recover_on_startup=False)
    rt = Runtime(settings)
    await rt.start()
    failures = 0
    try:
        tenant = "cli-demo"
        p = await rt.principals.upsert_principal(tenant, "cli_operator", PrincipalKind.OPERATOR,
            ["demo:run", "tx:read_all"], {})
        for name in names or list(SCENARIOS):
            result = await run(name, RunRequest(), p, rt)
            ok = result["state"] == result["expected_state"]
            failures += not ok
            print(json.dumps({"scenario": name, "state": result["state"], "expected": result["expected_state"],
                              "ok": ok, "root_id": result["root_id"],
                              "blocking_reasons": (result.get("commit_decision") or {}).get("blocking_reasons")}))
    finally:
        await rt.stop()
    return int(failures)


async def _crash_midflight() -> None:
    from app.api.demo import _make_offboarding
    from app.domain.transaction import CommitRequest
    from app.persistence.models import EffectRow
    from app.runtime import Runtime
    from app.worker import Worker

    async def hook(point: str, effect_id: UUID) -> None:
        if point != "after_external_call":
            return
        async with rt.db.read() as s:
            effect = await s.get(EffectRow, effect_id)
        if effect.contract_type == "billing.refund":
            print(json.dumps({"event": "SIMULATED_PROCESS_CRASH", "root_id": str(effect.root_id),
                              "effect_id": str(effect_id)}), flush=True)
            os._exit(137)

    settings = replace(Settings(), demo_mode=True, embedded_worker=False, auto_recover_on_startup=False)
    rt = Runtime(settings, crash_hook=hook)
    await rt.start()
    cid = os.environ.get("PACT_CRASH_CUSTOMER") or f"C-CRASH-{uuid.uuid4().hex[:6].upper()}"
    try:
        (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
        actor, root = await _make_offboarding(rt, "cli-demo", cid, "143.27", False)
        frozen = await rt.coordinator.prepare(actor, root)
        assert frozen["status"] == "FROZEN", frozen
        await rt.coordinator.request_commit(actor, root, CommitRequest(revision_digest=frozen["digest"]))
        print(json.dumps({"event": "ROOT_CREATED", "root_id": str(root), "customer_id": cid}), flush=True)
        worker = Worker(rt)
        for _ in range(60):
            await worker.run_once(root)
        raise RuntimeError("crash hook did not run")
    finally:
        await rt.stop()


async def _recover() -> None:
    from app.runtime import Runtime
    from app.worker import Worker
    from sqlalchemy import select
    from app.persistence.models import TransactionRow

    rt = Runtime(Settings())
    await rt.start()
    try:
        await rt.coordinator.sweep(stranded_grace_s=0)
        worker = Worker(rt)
        for _ in range(400):
            progressed = await worker.run_once()
            if not progressed:
                await asyncio.sleep(0.05)
        async with rt.db.read() as s:
            rows = (await s.execute(select(TransactionRow).where(TransactionRow.parent_id.is_(None))
                                    .order_by(TransactionRow.created_at.desc()).limit(20))).scalars()
            print(json.dumps({"event": "RECOVERY_CHECKPOINT", "recent_roots": [
                {"root_id": str(t.id), "state": t.state} for t in rows]}))
    finally:
        await rt.stop()


async def _planner_smoke(intent: str) -> int:
    from app.agents.model_provider import build_planner
    from app.domain.errors import ValidationFailed

    try:
        settings = Settings()
        planner = build_planner(settings)
        proposal = await planner.propose_transaction(intent, {})
    except ValidationFailed as exc:
        print(json.dumps({"error": exc.to_dict(), "applied": False}))
        return 1
    print(json.dumps({"provider": planner.name,
                      "live": planner.name != "deterministic_fixture",
                      "model": getattr(planner, "model", None),
                      "request_id": getattr(planner, "last_request_id", None),
                      "usage": getattr(planner, "last_usage", None),
                      "proposal": proposal.model_dump(mode="json"),
                      "applied": False}, ensure_ascii=False))
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "migrate":
        _migrate()
        return 0
    if cmd in {"create-operator", "create-agent"}:
        return asyncio.run(_provision(cmd, rest))
    if cmd == "scenario":
        return asyncio.run(_scenarios(rest))
    if cmd == "crash-midflight":
        asyncio.run(_crash_midflight())
        return 0
    if cmd == "recover":
        asyncio.run(_recover())
        return 0
    if cmd == "planner-smoke" and len(rest) == 1:
        return asyncio.run(_planner_smoke(rest[0]))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
