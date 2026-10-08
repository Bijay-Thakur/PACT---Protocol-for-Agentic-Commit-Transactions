"""Create synthetic requester/approver logins and provider data in disposable DB."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
from scripts.run_disposable_app import configure  # noqa: E402
from app.config import Settings  # noqa: E402
from app.domain.enums import PrincipalKind  # noqa: E402
from app.runtime import Runtime  # noqa: E402


async def main() -> None:
    rt = Runtime(Settings())
    await rt.start()
    try:
        tenant = "phase21-browser"
        password = "phase21-fixture-password"
        requester = await rt.principals.upsert_principal(tenant, "requester", PrincipalKind.OPERATOR,
            ["planner:propose", "tx:begin", "tx:propose", "tx:prepare", "tx:commit"],
            {"workflows": {"customer_offboarding": {"amount_limit": "500.00",
                                                      "approval_threshold": "100.00"}}})
        approver = await rt.principals.upsert_principal(tenant, "approver", PrincipalKind.OPERATOR,
            ["op:approve", "op:recover", "demo:run", "tx:read_all"], {"roles": ["refund_approver"]})
        await rt.principals.set_password(requester.id, requester.name, password)
        await rt.principals.set_password(approver.id, approver.name, password)
        customer_id = os.environ.get("PACT_BROWSER_CUSTOMER_ID", "C-BROWSER-21")
        for cid in (customer_id, f"{customer_id}-REVISE", f"{customer_id}-MISMATCH",
                    f"{customer_id}-CONTRADICT"):
            (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
        mismatch_id = f"{customer_id}-MISMATCH"
        (await rt.http.post("/sim/faults", json={"customer_id": mismatch_id, "system": "billing",
            "operation": "create_refund", "mode": "apply_wrong_amount",
            "params": {"applied_amount": "99.00"}})).raise_for_status()
        print(f"Synthetic browser fixture: tenant phase21-browser, requester/approver, customers {customer_id}, {customer_id}-REVISE and {mismatch_id}")
    finally:
        await rt.stop()


if __name__ == "__main__":
    configure()
    asyncio.run(main())
