"""Capture a populated Phase 1 database as a JSON fixture (run ONCE, with Phase 1 code).

Used by the A44 upgrade test: the fixture is loaded into a database migrated to
revision 0001, then upgraded to head. It contains terminal transactions
(COMMITTED_VERIFIED, ABORTED, COMPENSATED) and in-flight ones (UNKNOWN,
HUMAN_REQUIRED) produced by the real Phase 1 runtime.

    PACT_FIXTURE_DB=postgresql+asyncpg://pact:pact@localhost:55432/pact_p1fixture_test \
        python tests/fixtures/make_phase1_fixture.py
"""

from __future__ import annotations

import asyncio
import datetime
import decimal
import json
import os
import sys
import uuid
from pathlib import Path

import asyncpg
import httpx

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
URL = os.environ["PACT_FIXTURE_DB"]
TABLES = ["transactions", "capabilities", "logical_operations", "effects", "effect_dependencies", "invariants",
          "invariant_evaluations", "operation_attempts", "transaction_events", "commit_decisions",
          "operator_actions", "receipts"]


def _enc(o):
    if isinstance(o, (datetime.datetime, datetime.date)):
        return {"__dt__": o.isoformat()}
    if isinstance(o, uuid.UUID):
        return str(o)
    if isinstance(o, decimal.Decimal):
        return {"__dec__": str(o)}
    raise TypeError(type(o))


async def main() -> None:
    raw = URL.replace("+asyncpg", "")
    admin = await asyncpg.connect(raw.rsplit("/", 1)[0] + "/postgres")
    name = raw.rsplit("/", 1)[1]
    await admin.execute(f"DROP DATABASE IF EXISTS {name}")
    await admin.execute(f"CREATE DATABASE {name}")
    await admin.close()

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.attributes["url"] = URL
    await asyncio.to_thread(command.upgrade, cfg, "0001")

    os.environ["PACT_DATABASE_URL"] = URL
    os.environ["PACT_AUTO_RECOVER_ON_STARTUP"] = "false"
    os.environ["PACT_ADAPTER_TIMEOUT_S"] = "0.5"
    from app.config import Settings
    from app.main import create_app

    app = create_app(Settings())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://p", timeout=120) as c:
            for scenario, body in [("success", {}), ("budget-conflict", {}), ("compensation", {}),
                                   ("compensation-failure", {}), ("unknown", {"pause_at_unknown": True})]:
                r = (await c.post(f"/api/v1/demo/run/{scenario}", json=body)).json()
                print(scenario, r["state"])

    conn = await asyncpg.connect(raw)
    data = {}
    for t in TABLES:
        rows = await conn.fetch(f"SELECT * FROM {t}")
        data[t] = [dict(r) for r in rows]
    await conn.close()
    out = Path(__file__).with_name("phase1_db.json")
    out.write_text(json.dumps({"alembic_revision": "0001", "tables": data}, default=_enc, indent=0))
    print("wrote", out, {t: len(v) for t, v in data.items()})


if __name__ == "__main__":
    asyncio.run(main())
