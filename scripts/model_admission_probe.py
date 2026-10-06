"""One process of the cross-process PostgreSQL model allowance regression."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.model_provider import PlanProposal  # noqa: E402
from app.api.planner import ProposeRequest, propose  # noqa: E402
from app.config import Settings  # noqa: E402
from app.domain.enums import PrincipalKind  # noqa: E402
from app.domain.errors import ValidationFailed  # noqa: E402
from app.persistence.db import Database  # noqa: E402
from app.security.principals import Principal  # noqa: E402


class FakeLive:
    name = "groq_dev"
    model = "cross-process-test"
    last_usage = None
    last_request_id = None
    last_latency_ms = None

    async def propose_transaction(self, intent, context):
        await asyncio.sleep(0.1)
        return PlanProposal(objective=intent, requested_workflow="customer_offboarding",
                            entity_references={"customer_id": "C-PROBE"})


async def main(tenant: str) -> None:
    settings = Settings(database_url=os.environ["PACT_TEST_DATABASE_URL"],
                        model_max_requests_per_hour=1, model_max_inflight=1)
    db = Database(settings.database_url)
    principal = Principal(uuid.uuid4(), tenant, "model-probe", PrincipalKind.SERVICE,
                          frozenset({"planner:propose"}))
    try:
        await propose(ProposeRequest(intent="Cancel customer C-PROBE"), principal,
                      SimpleNamespace(db=db, settings=settings, planner=FakeLive()))
        outcome = "PROPOSED"
    except ValidationFailed as exc:
        outcome = exc.code
    finally:
        await db.dispose()
    print(json.dumps({"outcome": outcome}))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
