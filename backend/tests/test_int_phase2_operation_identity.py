"""A13/A14: trusted operation identity survives caller key changes and binds payload."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.domain.effect import EffectProposal
from app.domain.enums import PrincipalKind
from app.domain.transaction import ApprovalRequest, BeginRequest, CommitRequest
from app.persistence.models import EffectRow, LogicalOperationRow, TransactionRow
from app.worker import Worker

from .conftest import requires_db


@requires_db
async def test_renamed_key_cannot_duplicate_and_new_epoch_is_distinct(rt):
    cid = f"C-IDENT-{uuid.uuid4().hex[:6].upper()}"
    incident = f"INC-{uuid.uuid4().hex[:6].upper()}"
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    agent = await rt.principals.upsert_principal(tenant, "agent", PrincipalKind.AGENT,
        ["tx:begin", "tx:propose", "tx:prepare", "tx:commit", "tx:new_epoch"],
        {"workflows": {"customer_remediation": {"remediation_budget": "200.00"}}})
    approver = await rt.principals.upsert_principal(tenant, "finance", PrincipalKind.OPERATOR,
        ["op:approve"], {"roles": ["finance_approver"]})
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    charge = (await rt.http.get(f"/sim/state/{cid}")).json()["billing"]["account"]["charges"][0]["id"]

    async def draft(amount: str, caller_key: str, *, new_epoch: bool = False):
        root = await rt.manager.begin(agent, BeginRequest(workflow="customer_remediation",
            business_request={"customer_id": cid, "incident_id": incident},
            new_business_epoch=new_epoch, epoch_reason="Authorized separate remediation" if new_epoch else None))
        effect_id, _ = await rt.manager.propose(agent, root, EffectProposal(
            effect_type="billing.refund", slot="refund_line", operation_key=caller_key,
            payload={"customer_id": cid, "charge_id": charge, "amount": amount}))
        return root, effect_id, await rt.coordinator.prepare(agent, root)

    first, first_effect, frozen = await draft("50.00", "client-first")
    assert frozen["status"] == "FROZEN", frozen
    await rt.coordinator.approve(approver, first, ApprovalRequest(
        revision_digest=frozen["digest"], reason="Reviewed first refund"))
    assert (await rt.coordinator.request_commit(agent, first,
        CommitRequest(revision_digest=frozen["digest"])))["status"] == "QUEUED"
    worker = Worker(rt)
    for _ in range(30):
        await worker.run_once(first)
        async with rt.db.read() as s:
            if (await s.get(TransactionRow, first)).state == "COMMITTED_VERIFIED":
                break
    async with rt.db.read() as s:
        assert (await s.get(TransactionRow, first)).state == "COMMITTED_VERIFIED"
        original = await s.get(EffectRow, first_effect)
        original_identity = original.operation_key
        original_fingerprint = original.payload_fingerprint

    same, _, replay = await draft("50.00", "renamed-client-key")
    assert replay["status"] == "REJECTED", replay
    assert any(i["code"] == "OPERATION_ALREADY_SATISFIED" for i in replay["issues"])

    different, _, conflict = await draft("49.00", "another-client-key")
    assert conflict["status"] == "REJECTED", conflict
    assert any(i["code"] == "OPERATION_FINGERPRINT_CONFLICT" for i in conflict["issues"])

    epoch_root, epoch_effect, epoch_plan = await draft("49.00", "renamed-client-key", new_epoch=True)
    assert epoch_plan["status"] == "FROZEN", epoch_plan
    async with rt.db.read() as s:
        new_effect = await s.get(EffectRow, epoch_effect)
        assert new_effect.operation_key != original_identity
        assert new_effect.operation_key.endswith("/e2")
        assert new_effect.payload_fingerprint != original_fingerprint
        assert len((await s.execute(select(LogicalOperationRow).where(
            LogicalOperationRow.operation_key == original_identity))).scalars().all()) == 1
    calls = (await rt.http.get("/sim/calls", params={"customer_id": cid,
        "operation": "create_refund"})).json()
    assert len(calls) == 1
