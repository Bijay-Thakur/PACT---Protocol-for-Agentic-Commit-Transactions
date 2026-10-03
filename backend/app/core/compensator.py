"""Compensation engine - one recovery strategy among several, not the product.

Compensates VERIFIED effects whose contracts honestly declare a compensation, in
reverse dependency order, verifies each compensation against external state,
and records - never hides - irreversible effects that cannot be undone. The
transaction ends COMPENSATED only when nothing committed remains un-restituted;
otherwise it escalates to HUMAN_REQUIRED. PACT never claims "rolled back".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select

from app.core.effect_graph import EffectGraph
from app.core.executor import ExecutionContext
from app.core.verifier import Verifier
from app.domain.enums import (
    AttemptKind,
    AttemptStatus,
    DispatchOutcome,
    EffectState,
    EventType,
    LogicalOperationStatus,
    VerificationStatus,
)
from app.persistence.models import LogicalOperationRow, OperationAttemptRow
from app.persistence.repositories import append_event, effect_view, load_tree, next_attempt_no
from app.telemetry.tracing import span


@dataclass
class CompensationReport:
    compensated: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    irreversible: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.failed and not self.irreversible


class Compensator:
    def __init__(self, ctx: ExecutionContext, verifier: Verifier):
        self.ctx = ctx
        self.verifier = verifier

    async def plan(self, root_id: UUID, *, retry_failed: bool) -> tuple[list[UUID], list[UUID]]:
        """Return (to_compensate, irreversible) effect ids in reverse dependency order."""
        async with self.ctx.db.read() as s:
            rows = await load_tree(s, root_id, lock=False)
            graph = EffectGraph(rows.snapshot().effects)
            to_comp, irreversible = [], []
            for node in graph.reverse_topological_order():
                eff = rows.effects[node.id]
                state = EffectState(eff.state)
                eligible = state in (EffectState.VERIFIED, EffectState.COMPENSATING) or (
                    retry_failed and state == EffectState.HUMAN_REQUIRED and eff.compensation_result is not None)
                if not eligible:
                    continue
                if self.ctx.registry.contract(eff.contract_type).compensable:
                    to_comp.append(eff.id)
                elif state == EffectState.VERIFIED:
                    irreversible.append(eff.id)
            return to_comp, irreversible

    async def run(self, root_id: UUID, *, retry_failed: bool = False) -> CompensationReport:
        c = self.ctx
        report = CompensationReport()
        to_comp, irreversible = await self.plan(root_id, retry_failed=retry_failed)
        async with c.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            append_event(s, rows.root, EventType.COMPENSATION_STARTED, {
                "order": [rows.effects[i].operation_key for i in to_comp],
                "irreversible": [rows.effects[i].operation_key for i in irreversible],
                "note": "reverse dependency order; only contracts declaring compensation are compensated",
            })
            for eid in irreversible:
                eff = rows.effects[eid]
                report.irreversible.append(eff.operation_key)
                append_event(s, rows.txs[eff.transaction_id], EventType.COMPENSATION_NOT_POSSIBLE, {
                    "operation_key": eff.operation_key, "effect_type": eff.contract_type,
                    "reversibility_class": eff.reversibility_class,
                    "detail": "effect is irreversible; it remains committed and is recorded on the receipt",
                }, effect_id=eid)
        for eid in to_comp:
            ok, key = await self.compensate_effect(root_id, eid)
            (report.compensated if ok else report.failed).append(key)
        return report

    async def compensate_effect(self, root_id: UUID, effect_id: UUID) -> tuple[bool, str]:
        c = self.ctx
        async with c.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            eff = rows.effects[effect_id]
            tx = rows.txs[eff.transaction_id]
            if eff.state != EffectState.COMPENSATING:
                c.manager.transition_effect(s, tx, eff, EffectState.COMPENSATING, "compensation dispatched",
                                            event_type=EventType.EFFECT_COMPENSATION_STARTED)
            attempt_no = await next_attempt_no(s, eff.logical_operation_id, AttemptKind.COMPENSATE)
            attempt = OperationAttemptRow(
                logical_operation_id=eff.logical_operation_id, effect_id=eff.id, kind=str(AttemptKind.COMPENSATE),
                attempt_no=attempt_no, status=str(AttemptStatus.INTENT_RECORDED),
                request={"operation_key": eff.operation_key, "compensation_of": eff.contract_type,
                         "idempotency_key": eff.provider_idempotency_key + "_comp"},
                started_at=c.manager.clock(),
            )
            s.add(attempt)
            await s.flush()
            attempt_id = attempt.id
            view = effect_view(eff)

        adapter = c.registry.adapter(view.effect_type)
        with span("effect.compensate", effect_id=view.id, root_id=root_id, operation_key=view.operation_key,
                  effect_type=view.effect_type, actor_id=view.actor_id):
            dispatch = await adapter.compensate(view, c.adapter_ctx(attempt_no))
            verification = None
            if dispatch.outcome in (DispatchOutcome.ACCEPTED, DispatchOutcome.RESPONSE_LOST):
                verification, _ = await self.verifier.observe(view, compensation=True)

        ok = verification is not None and verification.status == VerificationStatus.VERIFIED_SUCCESS
        async with c.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            eff = rows.effects[effect_id]
            tx = rows.txs[eff.transaction_id]
            lop = (await s.execute(select(LogicalOperationRow).where(
                LogicalOperationRow.id == eff.logical_operation_id).with_for_update())).scalar_one()
            attempt = await s.get(OperationAttemptRow, attempt_id)
            attempt.status = str(AttemptStatus(dispatch.outcome.value))
            attempt.response = {"dispatch": dispatch.model_dump(mode="json"),
                                "verification": verification.model_dump(mode="json") if verification else None}
            attempt.finished_at = c.manager.clock()
            record = {"dispatch": dispatch.model_dump(mode="json"),
                      "verification": verification.model_dump(mode="json") if verification else None,
                      "attempt_no": attempt_no}
            eff.compensation_result = record
            if ok:
                lop.status = str(LogicalOperationStatus.COMPENSATED)
                c.manager.transition_effect(s, tx, eff, EffectState.COMPENSATED,
                                            "compensation applied and verified against external state",
                                            event_type=EventType.EFFECT_COMPENSATED, compensation=record)
            else:
                c.manager.transition_effect(s, tx, eff, EffectState.HUMAN_REQUIRED,
                                            "compensation failed or could not be verified; original effect remains in place",
                                            event_type=EventType.EFFECT_COMPENSATION_FAILED, compensation=record)
            return ok, eff.operation_key
