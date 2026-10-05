"""Simulated provider surprises preserve application truth and do not blind-retry."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.adapters.billing_mock import CONTRACT as REFUND_CONTRACT
from app.core.evidence import normalize_negative, retry_justification
from app.domain.effect import EffectProposal
from app.domain.enums import Application, Consistency, Postcondition, PrincipalKind
from app.domain.transaction import ApprovalRequest, BeginRequest, CommitRequest, OperatorActionRequest, RecoveryPolicy
from app.domain.verification import Observation
from app.persistence.models import EffectRow, ObservationRow, ReceiptAmendmentRow, ReceiptRow, ReservationRow, ResidualObligationRow, TransactionRow
from app.worker import Worker
from app.api.demo import _make_offboarding

from .conftest import requires_db


@pytest.mark.parametrize("mode,expected_state,expected_count", [
    ("apply_wrong_currency", "HUMAN_REQUIRED", 1),
    ("apply_wrong_charge", "HUMAN_REQUIRED", 1),
    ("apply_wrong_customer", "HUMAN_REQUIRED", 1),
    ("apply_wrong_status", "HUMAN_REQUIRED", 1),
    ("apply_duplicate", "HUMAN_REQUIRED", 1),
    ("apply_then_503", "COMMITTED_VERIFIED", 1),
    ("drop_response_after_apply", "COMMITTED_VERIFIED", 1),
])
@requires_db
async def test_refund_provider_adverse_cases(rt, mode, expected_state, expected_count):
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    cid = f"C-ADV-{uuid.uuid4().hex[:6].upper()}"
    incident = f"INC-{uuid.uuid4().hex[:6].upper()}"
    agent = await rt.principals.upsert_principal(tenant, "agent", PrincipalKind.AGENT,
        ["tx:begin", "tx:propose", "tx:prepare", "tx:commit"],
        {"workflows": {"customer_remediation": {"remediation_budget": "100.00"}}})
    approver = await rt.principals.upsert_principal(tenant, "finance", PrincipalKind.OPERATOR,
        ["op:approve", "op:recover", "op:attest"], {"roles": ["finance_approver"]})
    (await rt.http.post("/sim/seed", json={"customer_id": cid,
        "profile": "budget" if mode == "apply_wrong_charge" else "standard"})).raise_for_status()
    charge = (await rt.http.get(f"/sim/state/{cid}")).json()["billing"]["account"]["charges"][0]["id"]
    root = await rt.manager.begin(agent, BeginRequest(workflow="customer_remediation",
        business_request={"customer_id": cid, "incident_id": incident}))
    effect_id, _ = await rt.manager.propose(agent, root, EffectProposal(
        effect_type="billing.refund", slot="refund_line",
        payload={"customer_id": cid, "charge_id": charge, "amount": "50.00"}))
    frozen = await rt.coordinator.prepare(agent, root)
    assert frozen["status"] == "FROZEN", frozen
    (await rt.http.post("/sim/faults", json={"customer_id": cid, "system": "billing",
        "operation": "create_refund", "mode": mode})).raise_for_status()
    await rt.coordinator.approve(approver, root, ApprovalRequest(
        revision_digest=frozen["digest"], reason="Reviewed adverse-case refund"))
    assert (await rt.coordinator.request_commit(agent, root,
        CommitRequest(revision_digest=frozen["digest"])))["status"] == "QUEUED"
    worker = Worker(rt)
    state = None
    saw_unknown_hold = False
    for _ in range(100):
        await worker.run_once(root)
        async with rt.db.read() as session:
            state = (await session.get(TransactionRow, root)).state
            effect = await session.get(EffectRow, effect_id)
            if effect.state == "UNKNOWN":
                scopes = await rt.budgets.scopes_for_effect(session, root, effect_id)
                for scope in scopes:
                    if await rt.budgets.effect_hold(session, scope.id, effect_id) >= 50:
                        saw_unknown_hold = True
        if state in {"COMMITTED_VERIFIED", "HUMAN_REQUIRED"}:
            break
        await asyncio.sleep(0.05)
    assert state == expected_state, (mode, state)
    calls = (await rt.http.get("/sim/calls", params={"customer_id": cid,
        "operation": "create_refund"})).json()
    assert len(calls) == expected_count, calls
    if mode == "drop_response_after_apply":
        assert saw_unknown_hold
    async with rt.db.read() as session:
        effect = await session.get(EffectRow, effect_id)
        assert effect.application == "APPLIED"
        clean = mode in {"apply_then_503", "drop_response_after_apply"}
        assert effect.postcondition == ("MATCH" if clean else "MISMATCH")
        residuals = (await session.execute(select(ResidualObligationRow).where(
            ResidualObligationRow.effect_id == effect_id))).scalars().all()
        if not clean:
            assert residuals
        if mode == "drop_response_after_apply":
            scopes = await rt.budgets.scopes_for_effect(session, root, effect_id)
            assert scopes
            for scope in scopes:
                consumed, held = await rt.budgets.balance(session, scope.id)
                assert consumed == 50
                assert held == 0
    if mode == "apply_wrong_currency":
        # A30/A31: finalization freezes the adverse receipt. A later human
        # attestation is linked as a new fact; it does not rewrite provider truth.
        await rt.coordinator.operator_action(approver, root, OperatorActionRequest(
            action="FINALIZE_FAILED", reason="Provider currency mismatch requires manual closure"))
        async with rt.db.read() as session:
            receipt = (await session.execute(select(ReceiptRow).where(
                ReceiptRow.transaction_id == root))).scalar_one()
            original_hash = receipt.sha256
            original_payload = receipt.payload
            assert original_payload["effects"][0]["postcondition"] == "MISMATCH"
            assert original_payload["residual_obligations"][0]["disposition"] == "OPEN"
        await rt.coordinator.operator_action(approver, root, OperatorActionRequest(
            action="ATTEST_RESIDUAL", residual_id=str(residuals[0].id),
            reason="Finance reviewed the external mismatch", evidence_reference="CASE-123"))
        async with rt.db.read() as session:
            receipt = (await session.execute(select(ReceiptRow).where(
                ReceiptRow.transaction_id == root))).scalar_one()
            amendment = (await session.execute(select(ReceiptAmendmentRow).where(
                ReceiptAmendmentRow.transaction_id == root))).scalar_one()
            residual = await session.get(ResidualObligationRow, residuals[0].id)
            assert receipt.sha256 == original_hash and receipt.payload == original_payload
            assert amendment.previous_hash == original_hash
            assert amendment.payload["payload"]["attested_by"] == approver.name
            assert residual.disposition == "ATTESTED"
            assert residual.resolution["note"] == "human attestation - not provider-verified"


def test_negative_evidence_waits_for_inflight_window_and_expired_dedup():
    now = datetime.now(UTC)
    absent = Observation(application=Application.UNKNOWN,
        postcondition=Postcondition.UNDETERMINED, consistency=Consistency.EVENTUAL,
        absent=True, pending=True, source="billing-sim")
    early, authoritative = normalize_negative(REFUND_CONTRACT, absent, ambiguous=True,
        sent_at=now - timedelta(seconds=1), now=now)
    assert early.application == Application.UNKNOWN and authoritative is False
    late, authoritative = normalize_negative(REFUND_CONTRACT, absent, ambiguous=True,
        sent_at=now - timedelta(seconds=4), now=now)
    assert late.application == Application.NOT_APPLIED_CONFIRMED and authoritative is True
    old_attempt = SimpleNamespace(authoritative=True,
        started_at=now - timedelta(seconds=REFUND_CONTRACT.idempotency_window_s + 10),
        request={"idempotency_key": "stable"})
    assert retry_justification(REFUND_CONTRACT, [old_attempt],
        negative_authoritative=False, now=now) is None
    assert retry_justification(REFUND_CONTRACT, [old_attempt],
        negative_authoritative=True, now=now).startswith("AUTHORITATIVE_NEGATIVE_EVIDENCE")


@requires_db
async def test_delayed_empty_lookup_outlasts_poll_budget_without_proving_absence(rt):
    cid = f"C-DELAY-{uuid.uuid4().hex[:6].upper()}"
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    # This simulator delays visibility by read count rather than wall time.
    # Declare a matching lag before freezing the contract, so an empty read
    # after six polls is still within the provider's stated consistency window.
    rt.registry.contract("billing.refund").consistency_lag_s = 10.0
    agent = await rt.principals.upsert_principal(tenant, "agent", PrincipalKind.AGENT,
        ["tx:begin", "tx:propose", "tx:prepare", "tx:commit"],
        {"workflows": {"customer_remediation": {"remediation_budget": "100.00"}}})
    approver = await rt.principals.upsert_principal(tenant, "finance", PrincipalKind.OPERATOR,
        ["op:approve"], {"roles": ["finance_approver"]})
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    charge = (await rt.http.get(f"/sim/state/{cid}")).json()["billing"]["account"]["charges"][0]["id"]
    root = await rt.manager.begin(agent, BeginRequest(workflow="customer_remediation",
        business_request={"customer_id": cid, "incident_id": f"INC-{uuid.uuid4().hex[:6].upper()}"}))
    effect_id, _ = await rt.manager.propose(agent, root, EffectProposal(
        effect_type="billing.refund", slot="refund_line",
        payload={"customer_id": cid, "charge_id": charge, "amount": "50.00"}))
    frozen = await rt.coordinator.prepare(agent, root)
    assert frozen["status"] == "FROZEN", frozen
    (await rt.http.post("/sim/faults", json={"customer_id": cid, "system": "billing",
        "operation": "create_refund", "mode": "delay_visibility", "params": {"reads": 100}})).raise_for_status()
    await rt.coordinator.approve(approver, root, ApprovalRequest(
        revision_digest=frozen["digest"], reason="Reviewed delayed lookup"))
    assert (await rt.coordinator.request_commit(agent, root,
        CommitRequest(revision_digest=frozen["digest"])))["status"] == "QUEUED"
    worker = Worker(rt)
    for _ in range(12):
        await worker.run_once(root)
        async with rt.db.read() as session:
            if (await session.get(EffectRow, effect_id)).state == "UNKNOWN":
                break
    async with rt.db.read() as session:
        effect = await session.get(EffectRow, effect_id)
        assert effect.state == "UNKNOWN"
        assert effect.application == "UNKNOWN"
        assert effect.verification_result["negative_authoritative"] is False
        observations = (await session.execute(select(ObservationRow).where(
            ObservationRow.effect_id == effect_id))).scalars().all()
        assert any(o.purpose == "VERIFY" and o.application == "UNKNOWN" and
                   not o.negative_authoritative for o in observations)
    calls = (await rt.http.get("/sim/calls", params={"customer_id": cid,
        "operation": "create_refund"})).json()
    assert len(calls) == 1


@requires_db
async def test_unknown_hold_and_reservation_survive_terminal_closure(rt):
    cid = f"C-HOLD-{uuid.uuid4().hex[:6].upper()}"
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    agent = await rt.principals.upsert_principal(tenant, "agent", PrincipalKind.AGENT,
        ["tx:begin", "tx:propose", "tx:prepare", "tx:commit"],
        {"workflows": {"customer_remediation": {"remediation_budget": "100.00"}}})
    operator = await rt.principals.upsert_principal(tenant, "finance", PrincipalKind.OPERATOR,
        ["op:approve", "op:recover"], {"roles": ["finance_approver"]})
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    charge = (await rt.http.get(f"/sim/state/{cid}")).json()["billing"]["account"]["charges"][0]["id"]
    root = await rt.manager.begin(agent, BeginRequest(workflow="customer_remediation",
        business_request={"customer_id": cid, "incident_id": f"INC-{uuid.uuid4().hex[:6].upper()}"},
        recovery_policy=RecoveryPolicy(on_unknown="HUMAN_REQUIRED", auto_reconcile=False)))
    effect_id, _ = await rt.manager.propose(agent, root, EffectProposal(
        effect_type="billing.refund", slot="refund_line",
        payload={"customer_id": cid, "charge_id": charge, "amount": "50.00"}))
    frozen = await rt.coordinator.prepare(agent, root)
    assert frozen["status"] == "FROZEN", frozen
    (await rt.http.post("/sim/faults", json={"customer_id": cid, "system": "billing",
        "operation": "create_refund", "mode": "timeout_before_apply"})).raise_for_status()
    await rt.coordinator.approve(operator, root, ApprovalRequest(
        revision_digest=frozen["digest"], reason="Reviewed uncertain refund"))
    assert (await rt.coordinator.request_commit(agent, root,
        CommitRequest(revision_digest=frozen["digest"])))["status"] == "QUEUED"
    worker = Worker(rt)
    await worker.run_once(root)
    async with rt.db.read() as session:
        effect = await session.get(EffectRow, effect_id)
        assert effect.application == "UNKNOWN"
        scopes = await rt.budgets.scopes_for_effect(session, root, effect_id)
        assert scopes
        for scope in scopes:
            assert await rt.budgets.effect_hold(session, scope.id, effect_id) >= 50
    first_claim = await rt.queue.claim("lease-before-expiry", root_id=root)
    assert first_claim is not None
    await rt.queue.expire_lease_for_tests(root)
    replacement_claim = await rt.queue.claim("lease-after-expiry", root_id=root)
    assert replacement_claim is not None and replacement_claim.takeover
    async with rt.db.read() as session:
        for scope in scopes:
            assert await rt.budgets.effect_hold(session, scope.id, effect_id) >= 50
    delay = await rt.coordinator.step(replacement_claim)
    await rt.queue.finish(replacement_claim, next_delay_s=delay)
    for _ in range(20):
        await worker.run_once(root)
        async with rt.db.read() as session:
            state = (await session.get(TransactionRow, root)).state
        if state == "HUMAN_REQUIRED":
            break
    assert state == "HUMAN_REQUIRED"
    await rt.coordinator.operator_action(operator, root, OperatorActionRequest(
        action="FINALIZE_FAILED", reason="Unresolved provider outcome requires manual investigation"))
    async with rt.db.read() as session:
        assert (await session.get(TransactionRow, root)).state == "FAILED_TERMINAL"
        effect = await session.get(EffectRow, effect_id)
        assert effect.application == "UNKNOWN"
        scopes = await rt.budgets.scopes_for_effect(session, root, effect_id)
        assert scopes
        for scope in scopes:
            consumed, held = await rt.budgets.balance(session, scope.id)
            assert consumed == 0 and held >= 50
        reservations = (await session.execute(select(ReservationRow).where(
            ReservationRow.effect_id == effect_id))).scalars().all()
        assert reservations and all(r.status == "RETAINED" for r in reservations)


@pytest.mark.parametrize("system,operation,mode,effect_type", [
    ("notification", "send", "apply_wrong_recipient", "notification.send"),
    ("notification", "send", "apply_wrong_template", "notification.send"),
    ("notification", "send", "apply_duplicate", "notification.send"),
    ("crm", "update", "apply_partial", "crm.update"),
])
@requires_db
async def test_wrong_recipient_or_partial_application_is_recorded(rt, system, operation, mode, effect_type):
    cid = f"C-ADV-{uuid.uuid4().hex[:6].upper()}"
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    (await rt.http.post("/sim/faults", json={"customer_id": cid, "system": system,
        "operation": operation, "mode": mode})).raise_for_status()
    agent, root = await _make_offboarding(rt, tenant, cid, "143.27", False)
    frozen = await rt.coordinator.prepare(agent, root)
    assert frozen["status"] == "FROZEN", frozen
    assert (await rt.coordinator.request_commit(agent, root,
        CommitRequest(revision_digest=frozen["digest"])))["status"] == "QUEUED"
    worker = Worker(rt)
    for _ in range(120):
        await worker.run_once(root)
        async with rt.db.read() as session:
            state = (await session.get(TransactionRow, root)).state
        if state in {"COMMITTED_VERIFIED", "COMPENSATED", "HUMAN_REQUIRED", "FAILED_TERMINAL"}:
            break
        await asyncio.sleep(0.05)
    async with rt.db.read() as session:
        effect = (await session.execute(select(EffectRow).where(
            EffectRow.root_id == root, EffectRow.contract_type == effect_type))).scalar_one()
        assert effect.application == "APPLIED", (mode, effect.application)
        assert effect.postcondition in {"MISMATCH", "PARTIAL"}, (mode, effect.postcondition)
        residuals = (await session.execute(select(ResidualObligationRow).where(
            ResidualObligationRow.effect_id == effect.id))).scalars().all()
        assert residuals, mode
    assert state != "COMMITTED_VERIFIED"
