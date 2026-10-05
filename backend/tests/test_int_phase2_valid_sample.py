"""A48: small varied valid-request sample for policy false-block detection."""

from __future__ import annotations

import asyncio
import uuid

from app.api.demo import _make_offboarding
from app.domain.effect import EffectProposal
from app.domain.enums import PrincipalKind
from app.domain.transaction import ApprovalRequest, BeginRequest, CommitRequest
from app.persistence.models import TransactionRow
from app.worker import Worker

from .conftest import requires_db


@requires_db
async def test_ten_valid_requests_have_no_false_policy_blocks(rt):
    cases = [("remediation", amount, "budget" if i % 2 else "standard")
             for i, amount in enumerate(["1.00", "10.00", "25.00", "49.99", "50.00", "75.00"])]
    cases += [("offboarding", "143.27", "standard") for _ in range(4)]
    failures = []
    worker = Worker(rt)
    for index, (workflow, amount, profile) in enumerate(cases):
        tenant = f"test-{uuid.uuid4().hex[:8]}"
        cid = f"C-VALID-{index}-{uuid.uuid4().hex[:6].upper()}"
        (await rt.http.post("/sim/seed", json={"customer_id": cid, "profile": profile})).raise_for_status()
        if workflow == "offboarding":
            agent, root = await _make_offboarding(rt, tenant, cid, amount, False)
            approver = None
        else:
            agent = await rt.principals.upsert_principal(tenant, "agent", PrincipalKind.AGENT,
                ["tx:begin", "tx:propose", "tx:prepare", "tx:commit"],
                {"workflows": {"customer_remediation": {"remediation_budget": "200.00"}}})
            approver = await rt.principals.upsert_principal(tenant, "finance", PrincipalKind.OPERATOR,
                ["op:approve"], {"roles": ["finance_approver"]})
            charge = (await rt.http.get(f"/sim/state/{cid}")).json()["billing"]["account"]["charges"][0]["id"]
            root = await rt.manager.begin(agent, BeginRequest(workflow="customer_remediation",
                business_request={"customer_id": cid, "incident_id": f"INC-{uuid.uuid4().hex[:6].upper()}"}))
            await rt.manager.propose(agent, root, EffectProposal(
                effect_type="billing.refund", slot="refund_line",
                payload={"customer_id": cid, "charge_id": charge, "amount": amount,
                         "reason": f"Valid remediation sample {index}"}))
        frozen = await rt.coordinator.prepare(agent, root)
        if frozen["status"] != "FROZEN":
            failures.append((index, workflow, "prepare", frozen))
            continue
        if approver is not None:
            await rt.coordinator.approve(approver, root, ApprovalRequest(
                revision_digest=frozen["digest"], reason="Reviewed valid sample"))
        decision = await rt.coordinator.request_commit(agent, root,
            CommitRequest(revision_digest=frozen["digest"]))
        if decision["status"] != "QUEUED":
            failures.append((index, workflow, "barrier", decision))
            continue
        for _ in range(90):
            await worker.run_once(root)
            async with rt.db.read() as session:
                state = (await session.get(TransactionRow, root)).state
            if state == "COMMITTED_VERIFIED":
                break
            await asyncio.sleep(0.02)
        if state != "COMMITTED_VERIFIED":
            failures.append((index, workflow, "completion", state))
    assert len(cases) == 10
    assert failures == [], f"false blocks: {len(failures)}/10; {failures}"
