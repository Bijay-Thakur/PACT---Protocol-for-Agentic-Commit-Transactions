"""Reconciliation engine: resolves UNKNOWN effects against external reality.

    UNKNOWN -> RECONCILING -> VERIFIED        postcondition holds (application recorded)
                           -> FAILED          applied but mismatching (residual) / authoritatively not applied
                                              when no retry is justified
                           -> RETRYABLE       retry justified by the contract (authoritative negative evidence,
                                              or replay inside the provider's idempotency dedup window)
                           -> UNKNOWN         still cannot tell (reconcile again later)
                           -> HUMAN_REQUIRED  conflicting evidence / no read path

Reconciliation never re-executes. The original request may still land after a
negative read; the negative-evidence rule only treats absence as authoritative
after the contract's in-flight window (+ consistency lag).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from app.core.evidence import execute_attempts, last_dispatch, normalize_negative, retry_justification
from app.core.executor import ExecutionContext
from app.core.verifier import Verifier
from app.core.work_queue import Claim
from app.domain.enums import (
    Application,
    AttemptKind,
    AttemptStatus,
    EffectState,
    EventType,
    LogicalOperationStatus,
    ObservationPurpose,
    Postcondition,
    ReconciliationOutcome,
    ReconciliationPolicy,
)
from app.persistence.models import LogicalOperationRow, OperationAttemptRow
from app.persistence.repositories import effect_view, load_tree, next_attempt_no
from app.telemetry.tracing import span


@dataclass
class ReconcileResult:
    outcome: str
    retry_after_s: float | None = None


class Reconciler:
    def __init__(self, ctx: ExecutionContext, verifier: Verifier):
        self.ctx = ctx
        self.verifier = verifier

    async def reconcile_effect(self, root_id: UUID, effect_id: UUID, claim: Claim | None) -> ReconcileResult:
        c = self.ctx
        async with c.db.uow() as s:
            await c.queue.fence(s, claim)
            rows = await load_tree(s, root_id, lock=True)
            eff = rows.effects[effect_id]
            tx = rows.txs[eff.transaction_id]
            if eff.state == EffectState.UNKNOWN:
                c.manager.transition_effect(s, tx, eff, EffectState.RECONCILING,
                                            "querying provider by operation identity (no re-execution)",
                                            event_type=EventType.RECONCILIATION_STARTED,
                                            idempotency_key=eff.provider_idempotency_key)
            attempt_no = await next_attempt_no(s, eff.logical_operation_id, AttemptKind.RECONCILE)
            attempt = OperationAttemptRow(
                logical_operation_id=eff.logical_operation_id, effect_id=eff.id, kind=str(AttemptKind.RECONCILE),
                attempt_no=attempt_no, status=str(AttemptStatus.INTENT_RECORDED), started_at=c.manager.clock(),
                work_item_id=claim.item_id if claim else None, worker_epoch=claim.epoch if claim else None,
                request={"query": "observe_by_operation_identity", "operation_key": eff.operation_key,
                         "idempotency_key": eff.provider_idempotency_key},
            )
            s.add(attempt)
            await s.flush()
            attempt_id = attempt.id
            view = effect_view(eff)
            attempts = await execute_attempts(s, eff.id)

        contract = c.registry.contract(view.effect_type)
        with span("effect.reconcile", effect_id=view.id, root_id=root_id, operation_key=view.operation_key,
                  effect_type=view.effect_type, actor_id=view.actor_id):
            obs, _ = await self.verifier.poll(view)
        ambiguous, sent_at = last_dispatch(attempts)
        obs, negative_auth = normalize_negative(contract, obs, ambiguous=ambiguous, sent_at=sent_at,
                                                now=c.manager.clock())
        justification = None
        retry_after = None
        if contract.reconciliation_policy == ReconciliationPolicy.MANUAL:
            outcome = ReconciliationOutcome.HUMAN_REQUIRED
        elif obs.postcondition == Postcondition.MATCH:
            outcome = ReconciliationOutcome.VERIFIED_SUCCESS
        elif obs.application == Application.APPLIED:
            outcome = "APPLIED_MISMATCH"
        elif obs.application == Application.NOT_APPLIED_CONFIRMED or (obs.absent and obs.readable):
            justification = retry_justification(contract, attempts, negative_authoritative=negative_auth,
                                                now=c.manager.clock())
            if justification:
                outcome = ReconciliationOutcome.SAFE_TO_RETRY
            else:
                outcome = ReconciliationOutcome.STILL_UNKNOWN
                window = contract.max_inflight_s + contract.consistency_lag_s
                retry_after = max(0.2, window - ((c.manager.clock() - sent_at).total_seconds() if sent_at else 0))
        else:
            outcome = ReconciliationOutcome.STILL_UNKNOWN
            retry_after = 1.0

        async with c.db.uow() as s:
            await c.queue.fence(s, claim)
            rows = await load_tree(s, root_id, lock=True)
            eff = rows.effects[effect_id]
            tx = rows.txs[eff.transaction_id]
            lop = (await s.execute(select(LogicalOperationRow).where(
                LogicalOperationRow.id == eff.logical_operation_id).with_for_update())).scalar_one()
            row = c.evidence.record_observation(s, eff, obs, ObservationPurpose.RECONCILE, attempt_id=attempt_id,
                                                negative_authoritative=negative_auth)
            await s.flush()
            attempt = await s.get(OperationAttemptRow, attempt_id)
            attempt.status = str(
                AttemptStatus.FOUND_APPLIED if obs.application == Application.APPLIED
                else AttemptStatus.FOUND_NOT_APPLIED if obs.application == Application.NOT_APPLIED_CONFIRMED
                else AttemptStatus.INDETERMINATE)
            attempt.response = {"observation_id": str(row.id), "application": str(obs.application),
                                "postcondition": str(obs.postcondition), "reason": obs.reason,
                                "negative_authoritative": negative_auth}
            attempt.finished_at = c.manager.clock()
            record = {"outcome": str(outcome), "observation_id": str(row.id), "application": str(obs.application),
                      "postcondition": str(obs.postcondition), "reason": obs.reason, "evidence": obs.evidence,
                      "negative_authoritative": negative_auth, "retry_justification": justification,
                      "attempt_no": attempt_no,
                      "last_execute_attempt_no": max((a.attempt_no for a in attempts), default=None)}
            eff.reconciliation_result = record
            await c.evidence.apply_outcome(s, tx, eff, obs, row)
            info = {"reconciliation": record}
            if outcome == ReconciliationOutcome.VERIFIED_SUCCESS:
                eff.verification_result = {"status": "VERIFIED_SUCCESS", "application": str(obs.application),
                                           "postcondition": str(obs.postcondition), "evidence": obs.evidence,
                                           "reason": obs.reason, "external_reference": obs.provider_reference,
                                           "observation_id": str(row.id), "source": "reconciliation",
                                           "provider_source": obs.source, "provenance": obs.provenance}
                eff.verified_at = c.manager.clock()
                lop.status = str(LogicalOperationStatus.VERIFIED)
                lop.provider_reference = eff.provider_reference
                lop.verified_result = eff.verification_result
                c.manager.transition_effect(s, tx, eff, EffectState.VERIFIED,
                                            "reconciliation found the target state; verified - no re-dispatch",
                                            event_type=EventType.RECONCILIATION_RESOLVED, **info)
            elif outcome == "APPLIED_MISMATCH":
                lop.status = str(LogicalOperationStatus.FAILED)
                c.manager.transition_effect(s, tx, eff, EffectState.FAILED,
                                            f"reconciliation found an applied mismatch: {obs.reason}",
                                            event_type=EventType.RECONCILIATION_RESOLVED, **info)
            elif outcome == ReconciliationOutcome.SAFE_TO_RETRY:
                lop.status = str(LogicalOperationStatus.RETRYABLE)
                c.manager.transition_effect(s, tx, eff, EffectState.RETRYABLE, f"SAFE_TO_RETRY: {justification}",
                                            event_type=EventType.RECONCILIATION_RESOLVED, **info)
            elif outcome == ReconciliationOutcome.HUMAN_REQUIRED:
                lop.status = str(LogicalOperationStatus.UNKNOWN)
                c.manager.transition_effect(s, tx, eff, EffectState.HUMAN_REQUIRED,
                                            "no authoritative read path; a human must resolve",
                                            event_type=EventType.RECONCILIATION_RESOLVED, **info)
            else:
                lop.status = str(LogicalOperationStatus.UNKNOWN)
                c.manager.transition_effect(s, tx, eff, EffectState.UNKNOWN, f"STILL_UNKNOWN: {obs.reason}",
                                            event_type=EventType.RECONCILIATION_RESOLVED, **info)
        return ReconcileResult(str(outcome), retry_after)
