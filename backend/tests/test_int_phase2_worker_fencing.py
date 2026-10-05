"""Real Postgres lease claims and stale epoch fencing with no timing sleeps."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.core.work_queue import StaleWorker
from app.domain.effect import EffectProposal
from app.domain.enums import PrincipalKind
from app.domain.transaction import ApprovalRequest, BeginRequest, CommitRequest, OperatorActionRequest, RecoveryPolicy
from app.persistence.models import EffectRow, OperationAttemptRow, TransactionRow
from app.worker import Worker

from .conftest import requires_db

pytestmark = requires_db


async def test_live_workers_do_not_steal_and_stale_epoch_cannot_finish(rt):
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    agent = await rt.principals.upsert_principal(tenant, "worker_agent", PrincipalKind.AGENT,
        ["tx:begin"], {"workflows": {"customer_offboarding": {"amount_limit": "0.00"}}})
    root = await rt.manager.begin(agent, BeginRequest(workflow="customer_offboarding",
        business_request={"customer_id": "C-WORKER"}))
    async with rt.db.uow() as session:
        await rt.queue.enqueue(session, tenant, root)
    a, b = await asyncio.gather(rt.queue.claim("worker-a", root_id=root),
                                rt.queue.claim("worker-b", root_id=root))
    assert (a is None) != (b is None)
    first = a or b
    assert first is not None
    other_id = "worker-b" if first.worker_id == "worker-a" else "worker-a"
    assert await rt.queue.claim(other_id, root_id=root) is None
    assert await rt.queue.heartbeat(first) is True
    await rt.queue.expire_lease_for_tests(root)
    second = await rt.queue.claim(other_id, root_id=root)
    assert second is not None and second.takeover is True
    assert second.epoch > first.epoch
    async with rt.db.uow() as session:
        with pytest.raises(StaleWorker):
            await rt.queue.fence(session, first)
    assert await rt.queue.heartbeat(first) is False
    assert await rt.queue.finish(first, next_delay_s=None) == "FENCED"
    assert await rt.queue.finish(second, next_delay_s=None) == "DONE"


async def test_late_provider_response_is_evidence_only_then_new_worker_reconciles(rt):
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    cid = f"C-LATE-{uuid.uuid4().hex[:6].upper()}"
    incident = f"INC-{uuid.uuid4().hex[:6].upper()}"
    agent = await rt.principals.upsert_principal(tenant, "agent", PrincipalKind.AGENT,
        ["tx:begin", "tx:propose", "tx:prepare", "tx:commit"],
        {"workflows": {"customer_remediation": {"remediation_budget": "100.00"}}})
    approver = await rt.principals.upsert_principal(tenant, "finance", PrincipalKind.OPERATOR,
        ["op:approve"], {"roles": ["finance_approver"]})
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    charge = (await rt.http.get(f"/sim/state/{cid}")).json()["billing"]["account"]["charges"][0]["id"]
    root = await rt.manager.begin(agent, BeginRequest(workflow="customer_remediation",
        business_request={"customer_id": cid, "incident_id": incident}))
    effect_id, _ = await rt.manager.propose(agent, root, EffectProposal(
        effect_type="billing.refund", slot="refund_line",
        payload={"customer_id": cid, "charge_id": charge, "amount": "50.00"}))
    frozen = await rt.coordinator.prepare(agent, root)
    assert frozen["status"] == "FROZEN"
    await rt.coordinator.approve(approver, root, ApprovalRequest(
        revision_digest=frozen["digest"], reason="Approved controlled refund"))
    assert (await rt.coordinator.request_commit(agent, root,
        CommitRequest(revision_digest=frozen["digest"])))["status"] == "QUEUED"

    takeover = None
    async def steal_after_external_call(point, called_effect_id):
        nonlocal takeover
        if point == "after_external_call" and called_effect_id == effect_id and takeover is None:
            await rt.queue.expire_lease_for_tests(root)
            takeover = await rt.queue.claim("worker-b", root_id=root)
            assert takeover is not None
    rt.exec_ctx.crash_hook = steal_after_external_call
    worker_a = Worker(rt, worker_id="worker-a")
    for _ in range(10):
        await worker_a.run_once(root)
        if takeover is not None:
            break
    assert takeover is not None
    async with rt.db.read() as session:
        effect = await session.get(EffectRow, effect_id)
        attempt = (await session.execute(select(OperationAttemptRow).where(
            OperationAttemptRow.effect_id == effect_id))).scalar_one()
        assert effect.state == "DISPATCHING"
        assert attempt.authoritative is False
        assert attempt.status == "STALE_LATE_RESPONSE"
    calls = (await rt.http.get("/sim/calls", params={"customer_id": cid,
        "operation": "create_refund"})).json()
    assert len(calls) == 1

    delay = await rt.coordinator.step(takeover)
    await rt.queue.finish(takeover, next_delay_s=delay)
    worker_b = Worker(rt, worker_id="worker-b")
    for _ in range(80):
        await worker_b.run_once(root)
        async with rt.db.read() as session:
            state = (await session.get(TransactionRow, root)).state
        if state == "COMMITTED_VERIFIED":
            break
        await asyncio.sleep(0.05)
    assert state == "COMMITTED_VERIFIED", state
    calls = (await rt.http.get("/sim/calls", params={"customer_id": cid,
        "operation": "create_refund"})).json()
    assert len(calls) == 1


async def test_worker_death_after_reconcile_lookup_replays_read_only(rt):
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    cid = f"C-RECON-{uuid.uuid4().hex[:6].upper()}"
    agent = await rt.principals.upsert_principal(tenant, "agent", PrincipalKind.AGENT,
        ["tx:begin", "tx:propose", "tx:prepare", "tx:commit"],
        {"workflows": {"customer_remediation": {"remediation_budget": "100.00"}}})
    operator = await rt.principals.upsert_principal(tenant, "finance", PrincipalKind.OPERATOR,
        ["op:approve", "op:recover"], {"roles": ["finance_approver"]})
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    charge = (await rt.http.get(f"/sim/state/{cid}")).json()["billing"]["account"]["charges"][0]["id"]
    root = await rt.manager.begin(agent, BeginRequest(workflow="customer_remediation",
        business_request={"customer_id": cid, "incident_id": f"INC-{uuid.uuid4().hex[:6].upper()}"},
        recovery_policy=RecoveryPolicy(auto_reconcile=False)))
    effect_id, _ = await rt.manager.propose(agent, root, EffectProposal(
        effect_type="billing.refund", slot="refund_line",
        payload={"customer_id": cid, "charge_id": charge, "amount": "50.00"}))
    frozen = await rt.coordinator.prepare(agent, root)
    assert frozen["status"] == "FROZEN", frozen
    (await rt.http.post("/sim/faults", json={"customer_id": cid, "system": "billing",
        "operation": "create_refund", "mode": "drop_response_after_apply"})).raise_for_status()
    await rt.coordinator.approve(operator, root, ApprovalRequest(
        revision_digest=frozen["digest"], reason="Reviewed refund"))
    assert (await rt.coordinator.request_commit(agent, root,
        CommitRequest(revision_digest=frozen["digest"])))["status"] == "QUEUED"
    worker = Worker(rt)
    for _ in range(15):
        await worker.run_once(root)
        async with rt.db.read() as session:
            state = (await session.get(TransactionRow, root)).state
        if state == "UNKNOWN":
            break
    assert state == "UNKNOWN"
    await rt.coordinator.operator_action(operator, root, OperatorActionRequest(
        action="RECONCILE", reason="Read external state after lost response"))
    original_poll = rt.coordinator.verifier.poll

    class SimulatedDeath(BaseException):
        pass

    async def die_after_lookup(view, **kwargs):
        await original_poll(view, **kwargs)
        raise SimulatedDeath()

    rt.coordinator.verifier.poll = die_after_lookup
    claim = await rt.queue.claim("worker-before-death", root_id=root)
    assert claim is not None
    try:
        with pytest.raises(SimulatedDeath):
            await rt.coordinator.step(claim)
    finally:
        rt.coordinator.verifier.poll = original_poll
    async with rt.db.read() as session:
        attempts = (await session.execute(select(OperationAttemptRow).where(
            OperationAttemptRow.effect_id == effect_id,
            OperationAttemptRow.kind == "RECONCILE"))).scalars().all()
        assert len(attempts) == 1 and attempts[0].status == "INTENT_RECORDED"
    await rt.queue.expire_lease_for_tests(root)
    replacement = Worker(rt, worker_id="worker-after-death")
    for _ in range(30):
        await replacement.run_once(root)
        async with rt.db.read() as session:
            state = (await session.get(TransactionRow, root)).state
        if state == "COMMITTED_VERIFIED":
            break
    assert state == "COMMITTED_VERIFIED"
    async with rt.db.read() as session:
        attempts = (await session.execute(select(OperationAttemptRow).where(
            OperationAttemptRow.effect_id == effect_id,
            OperationAttemptRow.kind == "RECONCILE").order_by(OperationAttemptRow.attempt_no))).scalars().all()
        assert [a.status for a in attempts] == ["ABANDONED_BY_RESTART", "FOUND_APPLIED"]
    calls = (await rt.http.get("/sim/calls", params={"customer_id": cid,
        "operation": "create_refund"})).json()
    assert len(calls) == 1


async def test_deaths_before_and_after_intent_do_not_duplicate_dispatch(rt):
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    cid = f"C-INTENT-{uuid.uuid4().hex[:6].upper()}"
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
    await rt.coordinator.approve(approver, root, ApprovalRequest(
        revision_digest=frozen["digest"], reason="Reviewed crash-window refund"))
    assert (await rt.coordinator.request_commit(agent, root,
        CommitRequest(revision_digest=frozen["digest"])))["status"] == "QUEUED"

    class SimulatedDeath(BaseException):
        pass

    original_dispatch = rt.coordinator.executor.dispatch

    async def before_intent(*args, **kwargs):
        raise SimulatedDeath()

    rt.coordinator.executor.dispatch = before_intent
    first = await rt.queue.claim("before-intent", root_id=root)
    assert first is not None
    try:
        with pytest.raises(SimulatedDeath):
            await rt.coordinator.step(first)
    finally:
        rt.coordinator.executor.dispatch = original_dispatch
    async with rt.db.read() as session:
        assert (await session.execute(select(OperationAttemptRow).where(
            OperationAttemptRow.effect_id == effect_id))).scalars().all() == []
    await rt.queue.expire_lease_for_tests(root)

    adapter = rt.registry.adapter("billing.refund")
    original_execute = adapter.execute

    async def after_intent_before_provider(*args, **kwargs):
        raise SimulatedDeath()

    adapter.execute = after_intent_before_provider
    second = await rt.queue.claim("after-intent", root_id=root)
    assert second is not None and second.takeover
    try:
        with pytest.raises(SimulatedDeath):
            await rt.coordinator.step(second)
    finally:
        adapter.execute = original_execute
    async with rt.db.read() as session:
        attempts = (await session.execute(select(OperationAttemptRow).where(
            OperationAttemptRow.effect_id == effect_id,
            OperationAttemptRow.kind == "EXECUTE"))).scalars().all()
        assert len(attempts) == 1 and attempts[0].status == "INTENT_RECORDED"
    calls = (await rt.http.get("/sim/calls", params={"customer_id": cid,
        "operation": "create_refund"})).json()
    assert calls == []
    await rt.queue.expire_lease_for_tests(root)
    replacement = Worker(rt, worker_id="after-restart")
    for _ in range(40):
        await replacement.run_once(root)
        async with rt.db.read() as session:
            state = (await session.get(TransactionRow, root)).state
        if state == "COMMITTED_VERIFIED":
            break
        await asyncio.sleep(0.05)
    assert state == "COMMITTED_VERIFIED"
    calls = (await rt.http.get("/sim/calls", params={"customer_id": cid,
        "operation": "create_refund"})).json()
    assert len(calls) == 1
