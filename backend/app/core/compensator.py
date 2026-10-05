"""Restoration (compensation) engine - one recovery strategy, with its own uncertainty.

Candidates are chosen by *outcome truth*, not by effect state: every effect whose
application is APPLIED (verified, or applied-but-mismatching) needs restoration
within the contract's declared scope; an effect whose causality is UNKNOWN
cannot be safely "undone" and becomes a residual for a human.

Each restoration has its own durable attempts and lifecycle:
- dispatch rejected as stale (412)      -> RESIDUAL (a later legitimate change exists; never overwritten)
- dispatch rejected definitively        -> COMPENSATION_FAILED residual, restoration PENDING (operator retry)
- dispatch ambiguous / accepted         -> observe restoration; if not evidenced, restoration UNKNOWN and the
                                           next step *reconciles* (observes) before any repeat
- irreversible applied effect           -> IRREVERSIBLE_CONSEQUENCE residual
- contract-declared retained history    -> RETAINED_HISTORY residual (accepted by policy, non-blocking)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select

from app.core.effect_graph import EffectGraph
from app.core.evidence import execute_attempts, last_dispatch, normalize_negative
from app.core.executor import ExecutionContext
from app.core.verifier import Verifier
from app.core.work_queue import Claim
from app.domain.enums import (
    Application,
    AttemptKind,
    AttemptStatus,
    DispatchOutcome,
    EffectState,
    EventType,
    LogicalOperationStatus,
    ObservationPurpose,
    ResidualDisposition,
    ResidualKind,
    Restoration,
)
from app.persistence.models import EffectRow, LogicalOperationRow, OperationAttemptRow, ResidualObligationRow
from app.persistence.repositories import TreeRows, append_event, effect_view, load_tree, next_attempt_no
from app.telemetry.tracing import span

RESTORABLE_STATES = {EffectState.VERIFIED, EffectState.FAILED, EffectState.COMPENSATING, EffectState.HUMAN_REQUIRED}


@dataclass
class RestorationPlan:
    next_effect: UUID | None
    residual_only: list[UUID] = field(default_factory=list)


class Compensator:
    def __init__(self, ctx: ExecutionContext, verifier: Verifier):
        self.ctx = ctx
        self.verifier = verifier

    def needs_restoration(self, eff: EffectRow) -> bool:
        return (eff.application == Application.APPLIED
                and eff.restoration in (Restoration.NOT_REQUIRED, Restoration.PENDING, Restoration.UNKNOWN)
                and EffectState(eff.state) in RESTORABLE_STATES)

    async def mark_scope(self, s, rows: TreeRows) -> list[str]:
        """Enter restoration: classify every applied consequence (called once, in the recovery decision)."""
        notes = []
        for eff in rows.effects.values():
            tx = rows.txs[eff.transaction_id]
            if eff.application == Application.UNKNOWN and EffectState(eff.state) == EffectState.VERIFIED:
                if eff.restoration == Restoration.NOT_REQUIRED:
                    eff.restoration = str(Restoration.RESIDUAL)
                    self.ctx.evidence.open_residual(
                        s, tx, eff, ResidualKind.UNKNOWN_OUTCOME,
                        "target state holds but nothing proves this operation caused it; not reversed automatically",
                        "Confirm with the provider who made the change before restoring.")
                    notes.append(eff.operation_key)
                continue
            if not self.needs_restoration(eff):
                continue
            contract = self.ctx.registry.contract(eff.contract_type)
            if not contract.compensable:
                eff.restoration = str(Restoration.RESIDUAL)
                self.ctx.evidence.open_residual(
                    s, tx, eff, ResidualKind.IRREVERSIBLE_CONSEQUENCE,
                    f"{eff.contract_type} is irreversible ({contract.restoration_scope}); it remains in effect",
                    "Accountable follow-up: remediate outside PACT or accept the consequence.",
                    amount=eff.observed_amount or eff.amount, currency=eff.currency)
                append_event(s, tx, EventType.COMPENSATION_NOT_POSSIBLE, {
                    "operation_key": eff.operation_key, "reversibility_class": eff.reversibility_class,
                    "detail": "irreversible effect remains in effect and is recorded as a residual"}, effect_id=eff.id)
            elif eff.restoration == Restoration.NOT_REQUIRED:
                eff.restoration = str(Restoration.PENDING)
        return notes

    def next_candidate(self, rows: TreeRows) -> UUID | None:
        graph = EffectGraph([n for n in rows.snapshot().effects if n.state != EffectState.ABORTED])
        for node in graph.reverse_topological_order():  # dependents are restored before what they depend on
            eff = rows.effects[node.id]
            if eff.restoration in (Restoration.PENDING, Restoration.UNKNOWN) and self.needs_restoration(eff) \
                    and EffectState(eff.state) != EffectState.HUMAN_REQUIRED:
                return eff.id
        return None

    async def restore_effect(self, root_id: UUID, effect_id: UUID, claim: Claim | None, *,
                             operator_retry: bool = False) -> str:
        c = self.ctx
        async with c.db.uow() as s:
            await c.queue.fence(s, claim)
            rows = await load_tree(s, root_id, lock=True)
            eff = rows.effects[effect_id]
            tx = rows.txs[eff.transaction_id]
            previous_unknown = eff.restoration == Restoration.UNKNOWN
            if EffectState(eff.state) != EffectState.COMPENSATING:
                c.manager.transition_effect(s, tx, eff, EffectState.COMPENSATING, "restoration started",
                                            event_type=EventType.EFFECT_COMPENSATION_STARTED)
            comp_attempts = await execute_attempts(s, eff.id, AttemptKind.COMPENSATE)
            view = effect_view(eff)
        adapter = c.registry.adapter(view.effect_type)

        if previous_unknown:
            # Reconcile the earlier restoration before any repeat (A23).
            obs, _ = await self.verifier.poll(view, restoration=True)
            ambiguous, sent_at = last_dispatch(comp_attempts)
            obs2, neg = normalize_negative(adapter.contract, obs.model_copy(update={
                "application": Application.NOT_APPLIED_CONFIRMED if obs.restoration == Restoration.RESIDUAL
                else obs.application}), ambiguous=ambiguous, sent_at=sent_at)
            async with c.db.uow() as s:
                await c.queue.fence(s, claim)
                rows = await load_tree(s, root_id, lock=True)
                eff = rows.effects[effect_id]
                tx = rows.txs[eff.transaction_id]
                row = c.evidence.record_observation(s, eff, obs, ObservationPurpose.COMPENSATION_VERIFY,
                                                    negative_authoritative=neg)
                await s.flush()
                if obs.restoration == Restoration.RESTORED:
                    return await self._restored(s, rows, eff, tx, obs, row.id)
                if not neg:
                    append_event(s, tx, EventType.RECONCILIATION_RESOLVED, {
                        "operation_key": eff.operation_key, "subject": "restoration",
                        "outcome": "STILL_UNKNOWN", "reason": obs.reason}, effect_id=eff.id)
                    return "RESTORATION_UNKNOWN"
                # Authoritatively not restored: a repeat with the same restoration key is now justified.

        async with c.db.uow() as s:
            await c.queue.fence(s, claim)
            rows = await load_tree(s, root_id, lock=True)
            eff = rows.effects[effect_id]
            attempt_no = await next_attempt_no(s, eff.logical_operation_id, AttemptKind.COMPENSATE)
            attempt = OperationAttemptRow(
                logical_operation_id=eff.logical_operation_id, effect_id=eff.id, kind=str(AttemptKind.COMPENSATE),
                attempt_no=attempt_no, status=str(AttemptStatus.INTENT_RECORDED), started_at=c.manager.clock(),
                work_item_id=claim.item_id if claim else None, worker_epoch=claim.epoch if claim else None,
                request={"operation_key": eff.operation_key, "restoration_of": eff.contract_type,
                         "idempotency_key": eff.provider_idempotency_key + "_restore", "operator_retry": operator_retry},
            )
            s.add(attempt)
            await s.flush()
            attempt_id = attempt.id
            view = effect_view(eff)
        with span("effect.compensate", effect_id=view.id, root_id=root_id, operation_key=view.operation_key,
                  effect_type=view.effect_type, actor_id=view.actor_id):
            dispatch = await adapter.restore(view, c.adapter_ctx(attempt_no))
            obs = None
            if dispatch.outcome in (DispatchOutcome.ACCEPTED, DispatchOutcome.RESPONSE_LOST):
                obs, _ = await self.verifier.poll(view, restoration=True)
        await c.crash_hook("after_restore_call", effect_id)

        async with c.db.uow() as s:
            await c.queue.fence(s, claim)
            rows = await load_tree(s, root_id, lock=True)
            eff = rows.effects[effect_id]
            tx = rows.txs[eff.transaction_id]
            attempt = await s.get(OperationAttemptRow, attempt_id)
            attempt.status = str(AttemptStatus(dispatch.outcome.value))
            attempt.response = {"dispatch": dispatch.model_dump(mode="json"),
                                "observation": obs.model_dump(mode="json") if obs else None}
            attempt.finished_at = c.manager.clock()
            row_id = None
            if obs is not None:
                row = c.evidence.record_observation(s, eff, obs, ObservationPurpose.COMPENSATION_VERIFY,
                                                    attempt_id=attempt_id)
                await s.flush()
                row_id = row.id
            eff.compensation_result = {"dispatch": dispatch.model_dump(mode="json"),
                                       "observation": obs.model_dump(mode="json") if obs else None,
                                       "observation_id": str(row_id) if row_id else None, "attempt_no": attempt_no}
            if obs is not None and obs.restoration == Restoration.RESTORED:
                return await self._restored(s, rows, eff, tx, obs, row_id)
            if dispatch.stale_precondition:
                eff.restoration = str(Restoration.RESIDUAL)
                c.evidence.open_residual(
                    s, tx, eff, ResidualKind.STALE_RESTORATION_BLOCKED,
                    "the record changed after PACT's operation; restoring the before-image would overwrite a "
                    "later legitimate change", "Review the current provider state and restore manually if needed.",
                    observation_id=row_id)
                c.manager.transition_effect(s, tx, eff, EffectState.HUMAN_REQUIRED,
                                            "restoration refused: provider state changed since PACT's operation",
                                            event_type=EventType.EFFECT_COMPENSATION_FAILED)
                return "STALE"
            if dispatch.outcome in (DispatchOutcome.RESPONSE_LOST,) or (obs is not None and not obs.readable):
                eff.restoration = str(Restoration.UNKNOWN)
                c.evidence.open_residual(s, tx, eff, ResidualKind.COMPENSATION_UNKNOWN,
                                         "restoration outcome unknown (response lost)",
                                         "Reconcile the restoration before repeating it.", observation_id=row_id)
                append_event(s, tx, EventType.EFFECT_COMPENSATION_FAILED, {
                    "operation_key": eff.operation_key, "restoration": "UNKNOWN",
                    "detail": "restoration response lost; will reconcile before any repeat"}, effect_id=eff.id)
                return "RESTORATION_UNKNOWN"
            eff.restoration = str(Restoration.PENDING)
            c.evidence.open_residual(
                s, tx, eff, ResidualKind.COMPENSATION_FAILED,
                f"restoration failed: {dispatch.error or (obs.reason if obs else dispatch.outcome)}",
                "Retry restoration after the provider issue is fixed, or remediate manually.", observation_id=row_id)
            c.manager.transition_effect(s, tx, eff, EffectState.HUMAN_REQUIRED,
                                        "restoration failed; original effect remains in place",
                                        event_type=EventType.EFFECT_COMPENSATION_FAILED,
                                        compensation=eff.compensation_result)
            return "FAILED"

    async def _restored(self, s, rows, eff, tx, obs, row_id) -> str:
        c = self.ctx
        eff.restoration = str(Restoration.RESTORED)
        lop = (await s.execute(select(LogicalOperationRow).where(
            LogicalOperationRow.id == eff.logical_operation_id).with_for_update())).scalar_one()
        lop.status = str(LogicalOperationStatus.COMPENSATED)
        # Earlier restoration residuals for this effect are resolved by this evidence.
        for r in (await s.execute(select(ResidualObligationRow).where(
                ResidualObligationRow.effect_id == eff.id, ResidualObligationRow.disposition == ResidualDisposition.OPEN,
                ResidualObligationRow.kind.in_([ResidualKind.COMPENSATION_UNKNOWN, ResidualKind.COMPENSATION_FAILED,
                                                ResidualKind.APPLIED_MISMATCH])))).scalars():
            r.disposition, r.resolved_at = str(ResidualDisposition.RESOLVED), c.manager.clock()
            r.resolution = {"by": "restoration_verified", "observation_id": str(row_id)}
        contract = c.registry.contract(eff.contract_type)
        for retained in contract.retained_history:
            c.evidence.open_residual(
                s, tx, eff, ResidualKind.RETAINED_HISTORY,
                f"{retained} written by this operation are kept by the provider as history",
                "None required: policy accepts retained audit history.", blocking=False, observation_id=row_id,
                disposition=ResidualDisposition.ACCEPTED)
        c.manager.transition_effect(s, tx, eff, EffectState.COMPENSATED,
                                    "restoration applied and verified against external state",
                                    event_type=EventType.EFFECT_COMPENSATED, compensation=eff.compensation_result)
        return "RESTORED"
