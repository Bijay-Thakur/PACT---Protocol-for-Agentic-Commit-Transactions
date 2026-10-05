"""Phase 2 end-to-end service flow through real Postgres and simulated provider."""

from __future__ import annotations

import uuid
import asyncio

from app.domain.effect import EffectProposal
from app.domain.enums import PrincipalKind
from app.domain.transaction import ApprovalRequest, BeginRequest, CommitRequest
from app.worker import Worker

from .conftest import requires_db

pytestmark = requires_db


async def test_remediation_approval_binding_and_durable_worker(rt):
    cid = f"C-P2-{uuid.uuid4().hex[:6].upper()}"
    incident = f"INC-{uuid.uuid4().hex[:6].upper()}"
    tenant = f"test-{uuid.uuid4().hex[:6]}"
    proposer = await rt.principals.upsert_principal(
        tenant, "refund_agent", PrincipalKind.AGENT,
        ["tx:begin", "tx:propose", "tx:prepare", "tx:commit"],
        {"workflows": {"customer_remediation": {"remediation_budget": "200.00"}}})
    approver = await rt.principals.upsert_principal(
        tenant, "finance", PrincipalKind.OPERATOR, ["op:approve", "tx:read_all"],
        {"roles": ["finance_approver"]})
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    state = (await rt.http.get(f"/sim/state/{cid}")).json()
    charge_id = state["billing"]["account"]["charges"][0]["id"]
    root = await rt.manager.begin(proposer, BeginRequest(
        workflow="customer_remediation", business_request={"customer_id": cid, "incident_id": incident}))
    await rt.manager.propose(proposer, root, EffectProposal(
        effect_type="billing.refund", slot="refund_line",
        payload={"customer_id": cid, "charge_id": charge_id, "amount": "50.00"}))
    frozen = await rt.coordinator.prepare(proposer, root)
    assert frozen["status"] == "FROZEN", frozen
    blocked = await rt.coordinator.request_commit(proposer, root, CommitRequest(revision_digest=frozen["digest"]))
    assert blocked["status"] == "AWAITING_APPROVAL", blocked
    await rt.coordinator.approve(approver, root, ApprovalRequest(
        revision_digest=frozen["digest"], reason="Reviewed remediation refund"))
    queued = await rt.coordinator.request_commit(proposer, root, CommitRequest(revision_digest=frozen["digest"]))
    assert queued["status"] == "QUEUED", queued
    worker = Worker(rt)
    for _ in range(25):
        if not await worker.run_once():
            break
    from app.persistence.models import TransactionRow
    async with rt.db.read() as s:
        actual = await s.get(TransactionRow, root)
        assert actual.state == "COMMITTED_VERIFIED", actual.state
    from app.services.query_service import QueryService
    verified = await QueryService(rt).verify_receipt(proposer, root)
    assert verified["valid"] is True
    # A29: the immutable receipt cites precisely the fresh observations used
    # by final verification, including their stored evidence digests.
    from sqlalchemy import select
    from app.persistence.models import ObservationRow, ReceiptRow
    async with rt.db.read() as s:
        tx = await s.get(TransactionRow, root)
        receipt = (await s.execute(select(ReceiptRow).where(
            ReceiptRow.transaction_id == root))).scalar_one()
        final_ids = set(tx.meta["final_observation_ids"])
        cited = {o["observation_id"]: o for o in receipt.payload["final_observation_set"]}
        assert set(cited) == final_ids
        assert final_ids
        for oid in final_ids:
            observation = await s.get(ObservationRow, uuid.UUID(oid))
            assert observation.purpose == "FINAL"
            assert cited[oid]["evidence_digest"] == observation.evidence_digest


async def test_applied_wrong_refund_retains_residual(rt):
    """A07: provider application with a wrong amount cannot end as clean restoration."""
    cid = f"C-P2-{uuid.uuid4().hex[:6].upper()}"
    tenant = f"test-{uuid.uuid4().hex[:6]}"
    p = await rt.principals.upsert_principal(
        tenant, "offboard_agent", PrincipalKind.AGENT,
        ["tx:begin", "tx:propose", "tx:prepare", "tx:commit"],
        {"workflows": {"customer_offboarding": {
            "amount_limit": "500.00", "cumulative_amount_limit": "500.00",
            "approval_threshold": "500.00"}}})
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    (await rt.http.post("/sim/faults", json={"customer_id": cid, "system": "billing",
        "operation": "create_refund", "mode": "apply_wrong_amount",
        "params": {"applied_amount": "99.00"}})).raise_for_status()
    root = await rt.manager.begin(p, BeginRequest(
        workflow="customer_offboarding", business_request={"customer_id": cid}))
    for slot, effect_type, payload in [
        ("cancel_subscription", "subscription.cancel", {"customer_id": cid}),
        ("revoke_premium", "identity.revoke", {"customer_id": cid}),
        ("mark_churned", "crm.update", {"customer_id": cid, "lifecycle_state": "churned",
                                               "note": "Cancelled via PACT"}),
        ("refund_unused", "billing.refund", {"customer_id": cid, "amount": "143.27"}),
        ("confirm_customer", "notification.send", {"customer_id": cid,
                                                      "template": "cancellation_confirmation"}),
    ]:
        await rt.manager.propose(p, root, EffectProposal(effect_type=effect_type, slot=slot, payload=payload))
    frozen = await rt.coordinator.prepare(p, root)
    assert frozen["status"] == "FROZEN", frozen
    queued = await rt.coordinator.request_commit(p, root, CommitRequest(revision_digest=frozen["digest"]))
    assert queued["status"] == "QUEUED", queued
    worker = Worker(rt)
    for _ in range(80):
        if not await worker.run_once():
            break
    from sqlalchemy import select
    from app.persistence.models import EffectRow, ResidualObligationRow, TransactionRow
    async with rt.db.read() as s:
        tx = await s.get(TransactionRow, root)
        refund = (await s.execute(select(EffectRow).where(
            EffectRow.root_id == root, EffectRow.contract_type == "billing.refund"))).scalar_one()
        residuals = (await s.execute(select(ResidualObligationRow).where(
            ResidualObligationRow.root_id == root))).scalars().all()
        assert tx.state == "HUMAN_REQUIRED", tx.state
        assert refund.application == "APPLIED"
        assert refund.postcondition == "MISMATCH"
        assert any(r.effect_id == refund.id for r in residuals)
    world = (await rt.http.get(f"/sim/state/{cid}")).json()
    assert [r["amount"] for r in world["billing"]["refunds"]] == ["99.00"]


async def test_overlapping_refund_resources_serialize_different_business_operations(rt):
    """A15/A17: concurrent barriers with distinct identities cannot reserve one charge twice."""
    cid = f"C-P2-{uuid.uuid4().hex[:6].upper()}"
    tenant = f"test-{uuid.uuid4().hex[:6]}"
    proposer = await rt.principals.upsert_principal(
        tenant, "refund_agent", PrincipalKind.AGENT,
        ["tx:begin", "tx:propose", "tx:prepare", "tx:commit"],
        {"workflows": {"customer_remediation": {"remediation_budget": "200.00"}}})
    approver = await rt.principals.upsert_principal(
        tenant, "finance", PrincipalKind.OPERATOR, ["op:approve"],
        {"roles": ["finance_approver"]})
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    charge = (await rt.http.get(f"/sim/state/{cid}")).json()["billing"]["account"]["charges"][0]["id"]
    prepared = []
    for _ in range(2):
        root = await rt.manager.begin(proposer, BeginRequest(workflow="customer_remediation",
            business_request={"customer_id": cid, "incident_id": f"INC-{uuid.uuid4().hex[:6].upper()}"}))
        await rt.manager.propose(proposer, root, EffectProposal(effect_type="billing.refund",
            slot="refund_line", payload={"customer_id": cid, "charge_id": charge, "amount": "50.00"}))
        frozen = await rt.coordinator.prepare(proposer, root)
        assert frozen["status"] == "FROZEN", frozen
        await rt.coordinator.approve(approver, root, ApprovalRequest(
            revision_digest=frozen["digest"], reason="Reviewed separate incident"))
        prepared.append((root, frozen["digest"]))
    decisions = await asyncio.gather(*(rt.coordinator.request_commit(
        proposer, root, CommitRequest(revision_digest=digest)) for root, digest in prepared))
    assert sorted(d["status"] for d in decisions) == ["BLOCKED", "QUEUED"], decisions


async def test_terminal_state_and_receipt_roll_back_together_on_write_failure(rt):
    """A20: a receipt write failure cannot commit the terminal state alone."""
    cid = f"C-ATOMIC-{uuid.uuid4().hex[:6].upper()}"
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    agent = await rt.principals.upsert_principal(tenant, "agent", PrincipalKind.AGENT,
        ["tx:begin", "tx:propose", "tx:prepare", "tx:commit"],
        {"workflows": {"customer_remediation": {"remediation_budget": "100.00"}}})
    approver = await rt.principals.upsert_principal(tenant, "finance", PrincipalKind.OPERATOR,
        ["op:approve"], {"roles": ["finance_approver"]})
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    charge = (await rt.http.get(f"/sim/state/{cid}")).json()["billing"]["account"]["charges"][0]["id"]
    root = await rt.manager.begin(agent, BeginRequest(workflow="customer_remediation",
        business_request={"customer_id": cid, "incident_id": f"INC-{uuid.uuid4().hex[:6].upper()}"}))
    await rt.manager.propose(agent, root, EffectProposal(effect_type="billing.refund", slot="refund_line",
        payload={"customer_id": cid, "charge_id": charge, "amount": "50.00"}))
    frozen = await rt.coordinator.prepare(agent, root)
    assert frozen["status"] == "FROZEN", frozen
    await rt.coordinator.approve(approver, root, ApprovalRequest(
        revision_digest=frozen["digest"], reason="Reviewed atomic receipt test"))
    assert (await rt.coordinator.request_commit(agent, root,
        CommitRequest(revision_digest=frozen["digest"])))["status"] == "QUEUED"

    original_finalize = rt.coordinator.receipts.finalize
    failed = False

    async def fail_once(session, rows, tx_id):
        nonlocal failed
        if not failed and tx_id == root and rows.root.state == "COMMITTED_VERIFIED":
            failed = True
            raise RuntimeError("injected receipt write failure")
        return await original_finalize(session, rows, tx_id)

    rt.coordinator.receipts.finalize = fail_once
    worker = Worker(rt)
    try:
        for _ in range(20):
            await worker.run_once(root)
            if failed:
                break
        assert failed
        from sqlalchemy import select
        from app.persistence.models import ReceiptRow, TransactionRow
        async with rt.db.read() as session:
            assert (await session.get(TransactionRow, root)).state == "VERIFYING"
            assert (await session.execute(select(ReceiptRow).where(
                ReceiptRow.transaction_id == root))).scalar_one_or_none() is None
    finally:
        rt.coordinator.receipts.finalize = original_finalize
    await asyncio.sleep(1.05)  # worker exception backoff
    for _ in range(15):
        await worker.run_once(root)
        async with rt.db.read() as session:
            state = (await session.get(TransactionRow, root)).state
        if state == "COMMITTED_VERIFIED":
            break
    assert state == "COMMITTED_VERIFIED"
    async with rt.db.read() as session:
        assert (await session.execute(select(ReceiptRow).where(
            ReceiptRow.transaction_id == root))).scalar_one().final_state == "COMMITTED_VERIFIED"
