"""Reconciliation engine: resolves UNKNOWN effects against external reality.

    UNKNOWN -> RECONCILING -> VERIFIED       (provider shows it applied; verified)
                           -> RETRYABLE      (provider authoritatively shows not applied)
                           -> UNKNOWN        (still cannot tell)
                           -> HUMAN_REQUIRED (conflicting evidence / no read path)

Reconciliation never re-executes the operation. A retry can only happen later,
from RETRYABLE, with the same provider idempotency key.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.core.executor import ExecutionContext
from app.core.verifier import Verifier
from app.domain.enums import (
    AttemptKind,
    AttemptStatus,
    EffectState,
    EventType,
    LogicalOperationStatus,
    ProviderFinding,
    ReconciliationOutcome,
    ReconciliationPolicy,
    VerificationStatus,
)
from app.domain.verification import ReconciliationFinding
from app.persistence.models import LogicalOperationRow, OperationAttemptRow
from app.persistence.repositories import effect_view, load_tree, next_attempt_no
from app.telemetry.tracing import span

FINDING_TO_ATTEMPT = {
    ProviderFinding.APPLIED: AttemptStatus.FOUND_APPLIED,
    ProviderFinding.NOT_APPLIED: AttemptStatus.FOUND_NOT_APPLIED,
    ProviderFinding.INDETERMINATE: AttemptStatus.INDETERMINATE,
    ProviderFinding.CONFLICTING: AttemptStatus.CONFLICTING,
}


class Reconciler:
    def __init__(self, ctx: ExecutionContext, verifier: Verifier):
        self.ctx = ctx
        self.verifier = verifier

    async def reconcile_effect(self, root_id: UUID, effect_id: UUID) -> ReconciliationOutcome:
        c = self.ctx
        async with c.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            eff = rows.effects[effect_id]
            tx = rows.txs[eff.transaction_id]
            c.manager.transition_effect(s, tx, eff, EffectState.RECONCILING,
                                        "querying provider by stable operation identity (no re-execution)",
                                        event_type=EventType.RECONCILIATION_STARTED,
                                        idempotency_key=eff.provider_idempotency_key)
            attempt_no = await next_attempt_no(s, eff.logical_operation_id, AttemptKind.RECONCILE)
            attempt = OperationAttemptRow(
                logical_operation_id=eff.logical_operation_id, effect_id=eff.id, kind=str(AttemptKind.RECONCILE),
                attempt_no=attempt_no, status=str(AttemptStatus.INTENT_RECORDED),
                request={"query": "lookup_by_operation_identity", "operation_key": eff.operation_key,
                         "idempotency_key": eff.provider_idempotency_key},
                started_at=c.manager.clock(),
            )
            s.add(attempt)
            await s.flush()
            attempt_id = attempt.id
            view = effect_view(eff)

        adapter = c.registry.adapter(view.effect_type)
        contract = adapter.contract
        verification = None
        with span("effect.reconcile", effect_id=view.id, root_id=root_id, operation_key=view.operation_key,
                  effect_type=view.effect_type, actor_id=view.actor_id):
            if contract.reconciliation_policy == ReconciliationPolicy.MANUAL:
                finding = ReconciliationFinding(finding=ProviderFinding.INDETERMINATE,
                                                reason="contract has no authoritative read path")
                outcome = ReconciliationOutcome.HUMAN_REQUIRED
            else:
                finding = await adapter.reconcile(view, c.adapter_ctx())
                if finding.finding == ProviderFinding.APPLIED:
                    verification, _ = await self.verifier.observe(view)
                    if verification.status == VerificationStatus.VERIFIED_SUCCESS:
                        outcome = ReconciliationOutcome.VERIFIED_SUCCESS
                    elif verification.status == VerificationStatus.VERIFIED_FAILURE:
                        outcome = ReconciliationOutcome.HUMAN_REQUIRED  # applied, but not as proposed
                    else:
                        outcome = ReconciliationOutcome.STILL_UNKNOWN
                elif finding.finding == ProviderFinding.NOT_APPLIED:
                    outcome = ReconciliationOutcome.SAFE_TO_RETRY
                elif finding.finding == ProviderFinding.CONFLICTING:
                    outcome = ReconciliationOutcome.HUMAN_REQUIRED
                else:
                    outcome = ReconciliationOutcome.STILL_UNKNOWN

        async with c.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            eff = rows.effects[effect_id]
            tx = rows.txs[eff.transaction_id]
            lop = (await s.execute(select(LogicalOperationRow).where(
                LogicalOperationRow.id == eff.logical_operation_id).with_for_update())).scalar_one()
            attempt = await s.get(OperationAttemptRow, attempt_id)
            attempt.status = str(FINDING_TO_ATTEMPT[finding.finding])
            attempt.response = finding.model_dump(mode="json")
            attempt.finished_at = c.manager.clock()
            record = {"outcome": str(outcome), "finding": finding.model_dump(mode="json"),
                      "verification": verification.model_dump(mode="json") if verification else None,
                      "attempt_no": attempt_no}
            eff.reconciliation_result = record
            info = {"reconciliation": record}
            if outcome == ReconciliationOutcome.VERIFIED_SUCCESS:
                eff.verification_result = {**verification.model_dump(mode="json"), "source": "reconciliation"}
                eff.verified_at = c.manager.clock()
                eff.provider_reference = verification.external_reference or finding.external_reference
                lop.status = str(LogicalOperationStatus.VERIFIED)
                lop.provider_reference = eff.provider_reference
                lop.verified_result = eff.verification_result
                c.manager.transition_effect(s, tx, eff, EffectState.VERIFIED,
                                            "reconciliation found the operation applied; verified - no duplicate dispatch",
                                            event_type=EventType.RECONCILIATION_RESOLVED, **info)
            elif outcome == ReconciliationOutcome.SAFE_TO_RETRY:
                lop.status = str(LogicalOperationStatus.RETRYABLE)
                c.manager.transition_effect(s, tx, eff, EffectState.RETRYABLE,
                                            "provider authoritatively reports the operation was not applied; SAFE_TO_RETRY",
                                            event_type=EventType.RECONCILIATION_RESOLVED, **info)
            elif outcome == ReconciliationOutcome.HUMAN_REQUIRED:
                lop.status = str(LogicalOperationStatus.UNKNOWN)
                c.manager.transition_effect(s, tx, eff, EffectState.HUMAN_REQUIRED,
                                            f"reconciliation cannot resolve safely: {finding.reason}",
                                            event_type=EventType.RECONCILIATION_RESOLVED, **info)
            else:
                lop.status = str(LogicalOperationStatus.UNKNOWN)
                c.manager.transition_effect(s, tx, eff, EffectState.UNKNOWN,
                                            f"STILL_UNKNOWN: {finding.reason}",
                                            event_type=EventType.RECONCILIATION_RESOLVED, **info)
        return outcome
