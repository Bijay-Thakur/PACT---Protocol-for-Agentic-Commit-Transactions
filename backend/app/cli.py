"""Command-line entry points.

    python -m app.cli migrate                      # alembic upgrade head
    python -m app.cli scenario <name> [...]        # run demo scenarios in-process
    python -m app.cli crash-midflight              # real process death after an external side effect
    python -m app.cli recover                      # fresh process resumes in-flight work from Postgres

``crash-midflight`` + ``recover`` prove restart recovery with an actual process
exit (``os._exit``), not a simulated exception: the billing provider has applied
the refund, PACT's process dies before persisting the response, and a new
process must reconcile from durable state without refunding twice.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from uuid import UUID

import httpx

from app.config import Settings


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations"))
    command.upgrade(cfg, "head")


async def _scenarios(names: list[str]) -> int:
    from app.main import create_app

    app = create_app(Settings())
    failures = 0
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://pact", timeout=180) as c:
            for name in names:
                r = await c.post(f"/api/v1/demo/run/{name}", json={})
                body = r.json()
                ok = r.status_code == 200 and body.get("matches_expected")
                failures += 0 if ok else 1
                print(json.dumps({"scenario": name, "ok": ok, "state": body.get("state"),
                                  "expected": body.get("expected_state"), "root_id": body.get("root_id"),
                                  "blocking_reasons": (body.get("commit_decision") or {}).get("blocking_reasons"),
                                  "provider_calls": body.get("provider_calls")}))
    return failures


async def _crash_midflight() -> None:
    """Commit a cancellation and kill the process right after the refund is applied externally."""
    from app.agents.client import HttpPactClient
    from app.agents.scripted_demo_agents import RootAgent, agents_from_specs
    from app.demo.specs import child_specs, root_spec
    from app.main import create_app
    from app.runtime import Runtime

    async def crash_hook(point: str, effect_id: UUID) -> None:
        if point == "after_external_call" and os.environ.get("PACT_CRASH_EFFECT_TYPE") in (None, "billing.refund"):
            async with rt.db.read() as s:
                from app.persistence.models import EffectRow
                eff = await s.get(EffectRow, effect_id)
            if eff.contract_type == "billing.refund":
                print(json.dumps({"event": "SIMULATED_PROCESS_CRASH", "effect_id": str(effect_id),
                                  "detail": "provider applied the refund; PACT process exits before persisting"}),
                      flush=True)
                os._exit(137)

    settings = Settings()
    rt = Runtime(settings, crash_hook=crash_hook)
    app = create_app(settings, runtime=rt)
    cid = os.environ.get("PACT_CRASH_CUSTOMER") or f"C-48291-CRASH-{uuid.uuid4().hex[:4].upper()}"
    async with app.router.lifespan_context(app):
        (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://pact", timeout=60) as c:
            pact = HttpPactClient(c)
            root = RootAgent()
            root_id = await root.open(pact, root_spec(cid, scenario="restart-recovery"))
            print(json.dumps({"event": "ROOT_CREATED", "root_id": root_id, "customer_id": cid}), flush=True)
            await root.delegate(pact, root_id, agents_from_specs(child_specs(cid)))
            await root.prepare_and_commit(pact, root_id)
    print(json.dumps({"event": "UNEXPECTED_COMPLETION"}), flush=True)
    sys.exit(2)


async def _recover() -> None:
    from app.runtime import Runtime

    rt = Runtime(Settings())
    await rt.start()
    try:
        resumed = await rt.coordinator.resume_inflight()
        out = []
        for rid in resumed:
            async with rt.db.read() as s:
                from app.persistence.repositories import get_tx
                out.append({"root_id": str(rid), "state": (await get_tx(s, rid)).state})
        print(json.dumps({"event": "RECOVERY_COMPLETE", "resumed": out}), flush=True)
    finally:
        await rt.stop()


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd, rest = argv[0], argv[1:]
    if cmd == "migrate":
        _migrate()
        return 0
    if cmd == "scenario":
        from app.demo.scenarios import SCENARIOS

        return asyncio.run(_scenarios(rest or list(SCENARIOS)))
    if cmd == "crash-midflight":
        asyncio.run(_crash_midflight())
        return 0
    if cmd == "recover":
        asyncio.run(_recover())
        return 0
    print(f"unknown command {cmd!r}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
