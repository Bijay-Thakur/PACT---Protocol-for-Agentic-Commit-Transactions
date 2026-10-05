"""Provider before-images and external edits remain authoritative during restoration."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.api.demo import _make_offboarding
from app.domain.effect import EffectView
from app.domain.enums import Application, EffectState, Postcondition, ReversibilityClass
from app.domain.transaction import CommitRequest
from app.persistence.models import EffectRow, OperationAttemptRow, ResidualObligationRow, TransactionRow
from app.worker import Worker

from .conftest import requires_db


@requires_db
async def test_existing_absent_entitlement_is_not_granted_during_compensation(rt):
    cid = f"C-REST-{uuid.uuid4().hex[:6].upper()}"
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    (await rt.http.post("/sim/external-change", json={"system": "identity",
        "customer_id": cid, "changes": {"entitlements": ["base"]}})).raise_for_status()
    (await rt.http.post("/sim/faults", json={"customer_id": cid, "system": "billing",
        "operation": "create_refund", "mode": "apply_wrong_amount",
        "params": {"applied_amount": "99.00"}})).raise_for_status()
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
        if state in {"HUMAN_REQUIRED", "COMPENSATED", "FAILED_TERMINAL"}:
            break
        await asyncio.sleep(0.05)
    async with rt.db.read() as session:
        revoke = (await session.execute(select(EffectRow).where(
            EffectRow.root_id == root, EffectRow.contract_type == "identity.revoke"))).scalar_one()
        assert revoke.application == "NOT_APPLIED_CONFIRMED"
        assert revoke.restoration == "NOT_REQUIRED"
    world = (await rt.http.get(f"/sim/state/{cid}")).json()
    assert "premium" not in world["identity"]["entitlements"]
    grants = (await rt.http.get("/sim/calls", params={"customer_id": cid,
        "system": "identity", "operation": "grant"})).json()
    assert grants == []


@requires_db
async def test_grant_already_present_is_recorded_as_provider_noop(rt):
    cid = f"C-PRESENT-{uuid.uuid4().hex[:6].upper()}"
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    adapter = rt.registry.adapter("identity.grant")
    now = datetime.now(UTC)
    view = EffectView(id=uuid.uuid4(), tenant_id="test", transaction_id=uuid.uuid4(),
        root_id=uuid.uuid4(), logical_operation_id=None, operation_key=f"grant:{cid}",
        effect_type="identity.grant", actor_id="test", state=EffectState.PREPARED,
        payload={"customer_id": cid, "entitlement": "premium"}, amount=None,
        resource_claims=[], depends_on=[], reversibility_class=ReversibilityClass.COMPENSABLE,
        provider_idempotency_key=f"grant-noop-{uuid.uuid4().hex}", provider_reference=None,
        created_at=now, updated_at=now)
    ctx = rt.exec_ctx.adapter_ctx()
    prepared = await adapter.prepare(view, ctx)
    assert prepared.ok and prepared.observations["had_entitlement"] is True
    view = view.model_copy(update={"prepare_evidence": {"observations": prepared.observations,
        "preconditions": prepared.preconditions}})
    dispatched = await adapter.execute(view, ctx)
    assert dispatched.outcome == "ACCEPTED"
    observed = await adapter.observe(view, ctx)
    assert observed.application == Application.NOT_APPLIED_CONFIRMED
    assert observed.postcondition == Postcondition.MATCH
    revokes = (await rt.http.get("/sim/calls", params={"customer_id": cid,
        "system": "identity", "operation": "revoke"})).json()
    assert revokes == []


@requires_db
async def test_external_edit_after_prepare_blocks_stale_commit(rt):
    cid = f"C-STALE-{uuid.uuid4().hex[:6].upper()}"
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    agent, root = await _make_offboarding(rt, tenant, cid, "143.27", False)
    frozen = await rt.coordinator.prepare(agent, root)
    assert frozen["status"] == "FROZEN", frozen
    (await rt.http.post("/sim/external-change", json={"system": "subscription",
        "customer_id": cid, "changes": {"plan": "changed-outside-pact"}})).raise_for_status()
    blocked = await rt.coordinator.request_commit(agent, root,
        CommitRequest(revision_digest=frozen["digest"]))
    assert blocked["status"] == "BLOCKED", blocked
    calls = (await rt.http.get("/sim/calls", params={"customer_id": cid,
        "operation": "cancel"})).json()
    assert calls == []


@requires_db
async def test_external_edit_after_effect_blocks_stale_restoration(rt):
    cid = f"C-LATE-{uuid.uuid4().hex[:6].upper()}"
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    (await rt.http.post("/sim/faults", json={"customer_id": cid, "system": "notification",
        "operation": "send", "mode": "apply_wrong_recipient"})).raise_for_status()
    agent, root = await _make_offboarding(rt, tenant, cid, "143.27", False)
    frozen = await rt.coordinator.prepare(agent, root)
    assert frozen["status"] == "FROZEN", frozen
    assert (await rt.coordinator.request_commit(agent, root,
        CommitRequest(revision_digest=frozen["digest"])))["status"] == "QUEUED"
    worker = Worker(rt)
    for _ in range(30):
        await worker.run_once(root)
        async with rt.db.read() as session:
            state = (await session.get(TransactionRow, root)).state
        if state == "COMPENSATING":
            break
    assert state == "COMPENSATING"
    changed = (await rt.http.post("/sim/external-change", json={"system": "subscription",
        "customer_id": cid, "changes": {"plan": "changed-after-cancel"}}))
    changed.raise_for_status()
    for _ in range(80):
        await worker.run_once(root)
        async with rt.db.read() as session:
            state = (await session.get(TransactionRow, root)).state
        if state == "HUMAN_REQUIRED":
            break
        await asyncio.sleep(0.05)
    assert state == "HUMAN_REQUIRED"
    async with rt.db.read() as session:
        cancel = (await session.execute(select(EffectRow).where(
            EffectRow.root_id == root, EffectRow.contract_type == "subscription.cancel"))).scalar_one()
        residuals = (await session.execute(select(ResidualObligationRow).where(
            ResidualObligationRow.effect_id == cancel.id))).scalars().all()
        assert cancel.restoration == "RESIDUAL"
        assert any(r.kind == "STALE_RESTORATION_BLOCKED" for r in residuals)
    world = (await rt.http.get(f"/sim/state/{cid}")).json()
    assert world["subscription"]["plan"] == "changed-after-cancel"


@requires_db
async def test_lost_restoration_response_does_not_repeat_inverse_call(rt):
    cid = f"C-COMP-{uuid.uuid4().hex[:6].upper()}"
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    for system, operation, mode in [
        ("notification", "send", "apply_wrong_recipient"),
        ("subscription", "reactivate", "drop_response_after_apply"),
    ]:
        (await rt.http.post("/sim/faults", json={"customer_id": cid,
            "system": system, "operation": operation, "mode": mode})).raise_for_status()
    agent, root = await _make_offboarding(rt, tenant, cid, "143.27", False)
    frozen = await rt.coordinator.prepare(agent, root)
    assert frozen["status"] == "FROZEN", frozen
    assert (await rt.coordinator.request_commit(agent, root,
        CommitRequest(revision_digest=frozen["digest"])))["status"] == "QUEUED"
    worker = Worker(rt)
    for _ in range(100):
        await worker.run_once(root)
        async with rt.db.read() as session:
            state = (await session.get(TransactionRow, root)).state
        if state in {"HUMAN_REQUIRED", "COMPENSATED", "FAILED_TERMINAL"}:
            break
        await asyncio.sleep(0.05)
    async with rt.db.read() as session:
        cancel = (await session.execute(select(EffectRow).where(
            EffectRow.root_id == root, EffectRow.contract_type == "subscription.cancel"))).scalar_one()
        assert cancel.restoration == "RESTORED"
    calls = (await rt.http.get("/sim/calls", params={"customer_id": cid,
        "system": "subscription", "operation": "reactivate"})).json()
    assert len(calls) == 1


@requires_db
async def test_worker_death_after_inverse_call_reconciles_before_repeat(rt):
    cid = f"C-CRASH-{uuid.uuid4().hex[:6].upper()}"
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    (await rt.http.post("/sim/faults", json={"customer_id": cid, "system": "notification",
        "operation": "send", "mode": "apply_wrong_recipient"})).raise_for_status()
    agent, root = await _make_offboarding(rt, tenant, cid, "143.27", False)
    frozen = await rt.coordinator.prepare(agent, root)
    assert frozen["status"] == "FROZEN", frozen
    assert (await rt.coordinator.request_commit(agent, root,
        CommitRequest(revision_digest=frozen["digest"])))["status"] == "QUEUED"
    worker = Worker(rt)
    for _ in range(30):
        await worker.run_once(root)
        async with rt.db.read() as session:
            state = (await session.get(TransactionRow, root)).state
        if state == "COMPENSATING":
            break
    assert state == "COMPENSATING"

    class SimulatedDeath(BaseException):
        pass

    crashed_effect = None

    async def die_after_inverse(point, effect_id):
        nonlocal crashed_effect
        if point == "after_restore_call":
            crashed_effect = effect_id
            raise SimulatedDeath()

    rt.exec_ctx.crash_hook = die_after_inverse
    claim = await rt.queue.claim("worker-before-death", root_id=root)
    assert claim is not None
    with pytest.raises(SimulatedDeath):
        await rt.coordinator.step(claim)
    assert crashed_effect is not None
    async with rt.db.read() as session:
        attempts = (await session.execute(select(OperationAttemptRow).where(
            OperationAttemptRow.effect_id == crashed_effect,
            OperationAttemptRow.kind == "COMPENSATE"))).scalars().all()
        assert len(attempts) == 1 and attempts[0].status == "INTENT_RECORDED"
    rt.exec_ctx.crash_hook = lambda *_: asyncio.sleep(0)
    await rt.queue.expire_lease_for_tests(root)
    replacement = Worker(rt, worker_id="worker-after-death")
    for _ in range(100):
        await replacement.run_once(root)
        async with rt.db.read() as session:
            state = (await session.get(TransactionRow, root)).state
        if state == "HUMAN_REQUIRED":
            break
        await asyncio.sleep(0.05)
    assert state == "HUMAN_REQUIRED"
    async with rt.db.read() as session:
        effect = await session.get(EffectRow, crashed_effect)
        attempts = (await session.execute(select(OperationAttemptRow).where(
            OperationAttemptRow.effect_id == crashed_effect,
            OperationAttemptRow.kind == "COMPENSATE"))).scalars().all()
        assert effect.restoration == "RESTORED"
        assert len(attempts) == 1
        assert attempts[0].status == "ABANDONED_BY_RESTART"
