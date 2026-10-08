"""Process-death points around durable intent. Business effects are counted at the provider."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.persistence.models import EffectRow, OperationAttemptRow, TransactionRow
from app.worker import Worker
from .test_int_phase21_fences import _refund_draft
from app.domain.transaction import ApprovalRequest, CommitRequest

from .conftest import requires_db

pytestmark = requires_db


class SimulatedCrash(BaseException):
    """Not an Exception, so the worker cannot swallow it."""


@pytest.mark.parametrize("point", ["before_intent", "after_intent", "after_external_call"])
async def test_crash_around_dispatch_preserves_one_business_effect(rt, point):
    requester, approver, root, effect_id, cid, _ = await _refund_draft(rt, point)
    frozen = await rt.coordinator.prepare(requester, root)
    assert frozen["status"] == "FROZEN", frozen
    await rt.coordinator.approve(approver, root, ApprovalRequest(
        revision_digest=frozen["digest"], reason="Reviewed before injected process death"))
    assert (await rt.coordinator.request_commit(
        requester, root, CommitRequest(revision_digest=frozen["digest"])))["status"] == "QUEUED"
    original = rt.exec_ctx.crash_hook
    crashed = False

    async def crash(where, called_effect):
        nonlocal crashed
        if where == point and called_effect == effect_id and not crashed:
            crashed = True
            raise SimulatedCrash()

    judge_calls = rt.coordinator.judge.calls
    rt.exec_ctx.crash_hook = crash
    try:
        with pytest.raises(SimulatedCrash):
            await Worker(rt, worker_id="worker-a").run_once(root)
        assert crashed
    finally:
        rt.exec_ctx.crash_hook = original
    await rt.queue.expire_lease_for_tests(root)
    worker = Worker(rt, worker_id="worker-b")
    state = None
    for _ in range(40):
        await worker.run_once(root)
        async with rt.db.read() as session:
            state = (await session.get(TransactionRow, root)).state
        if state in {"COMMITTED_VERIFIED", "HUMAN_REQUIRED", "FAILED_TERMINAL", "COMPENSATED"}:
            break
    calls = (await rt.http.get("/sim/calls", params={"customer_id": cid, "operation": "create_refund"})).json()
    assert len(calls) == 1
    assert state == "COMMITTED_VERIFIED"
    assert rt.coordinator.judge.calls == judge_calls
    async with rt.db.read() as session:
        attempts = list((await session.execute(select(OperationAttemptRow.status).join(
            EffectRow, EffectRow.id == OperationAttemptRow.effect_id).where(
                EffectRow.id == effect_id).order_by(OperationAttemptRow.started_at))).scalars())
    if point == "before_intent":
        assert "ABANDONED_BY_RESTART" not in attempts
    else:
        assert "ABANDONED_BY_RESTART" in attempts
        assert attempts.count("ACCEPTED") + attempts.count("FOUND_APPLIED") >= 1
