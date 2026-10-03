"""Verification engine: did the intended business state actually become true?

Verification always re-reads the external system through the adapter's read
path. It never reuses the executor's response. Eventual consistency is handled
by bounded polling; a result that stays pending becomes UNKNOWN (and therefore
goes to reconciliation), never an assumed success.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import select

from app.core.executor import ExecutionContext
from app.domain.effect import EffectView
from app.domain.enums import EffectState, EventType, LogicalOperationStatus, VerificationStatus
from app.domain.verification import VerificationResult
from app.persistence.models import LogicalOperationRow
from app.persistence.repositories import append_event, effect_view, load_tree
from app.telemetry.tracing import span


class Verifier:
    def __init__(self, ctx: ExecutionContext):
        self.ctx = ctx

    async def observe(self, view: EffectView, *, compensation: bool = False) -> tuple[VerificationResult, int]:
        """Poll the external read path until a non-pending answer or the poll budget ends."""
        adapter = self.ctx.registry.adapter(view.effect_type)
        contract = adapter.contract
        polls = 0
        result = VerificationResult(status=VerificationStatus.VERIFICATION_UNKNOWN, reason="not polled")
        with span("effect.verify", effect_id=view.id, root_id=view.root_id, operation_key=view.operation_key,
                  effect_type=view.effect_type, actor_id=view.actor_id) as sp:
            for i in range(max(1, contract.verification_polls)):
                polls = i + 1
                fn = adapter.verify_compensation if compensation else adapter.verify
                result = await fn(view, self.ctx.adapter_ctx())
                if result.status != VerificationStatus.VERIFICATION_PENDING:
                    break
                await asyncio.sleep(contract.verification_poll_interval_s)
            sp.set_attribute("pact.verification_status", str(result.status))
        if result.status == VerificationStatus.VERIFICATION_PENDING:
            result = VerificationResult(
                status=VerificationStatus.VERIFICATION_UNKNOWN, evidence=result.evidence,
                reason=f"still pending after {polls} polls: {result.reason}")
        return result, polls

    async def verify_effect(self, root_id: UUID, effect_id: UUID) -> VerificationStatus:
        c = self.ctx
        async with c.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            eff = rows.effects[effect_id]
            c.manager.transition_effect(s, rows.txs[eff.transaction_id], eff, EffectState.VERIFYING,
                                        "querying external system state independently of the dispatch response",
                                        event_type=EventType.VERIFICATION_STARTED)
            view = effect_view(eff)
        result, polls = await self.observe(view)
        async with c.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            eff = rows.effects[effect_id]
            tx = rows.txs[eff.transaction_id]
            lop = (await s.execute(select(LogicalOperationRow).where(
                LogicalOperationRow.id == eff.logical_operation_id).with_for_update())).scalar_one()
            record = {**result.model_dump(mode="json"), "polls": polls, "source": "verification"}
            eff.verification_result = record
            info = {"verification": record,
                    "dispatch_outcome": (eff.dispatch_result or {}).get("outcome")}
            if polls > 1:
                append_event(s, tx, EventType.VERIFICATION_PENDING,
                             {"operation_key": eff.operation_key, "polls": polls,
                              "detail": "external state not yet visible; dependents remain blocked"},
                             effect_id=eff.id)
            match result.status:
                case VerificationStatus.VERIFIED_SUCCESS:
                    eff.verified_at = c.manager.clock()
                    eff.provider_reference = result.external_reference or eff.provider_reference
                    lop.status = str(LogicalOperationStatus.VERIFIED)
                    lop.provider_reference = eff.provider_reference
                    lop.verified_result = record
                    c.manager.transition_effect(s, tx, eff, EffectState.VERIFIED,
                                                "BUSINESS_EFFECT_VERIFIED against external state",
                                                event_type=EventType.EFFECT_VERIFIED, **info)
                case VerificationStatus.VERIFIED_FAILURE:
                    lop.status = str(LogicalOperationStatus.FAILED)
                    c.manager.transition_effect(s, tx, eff, EffectState.FAILED,
                                                f"external state contradicts the dispatch response: {result.reason}",
                                                event_type=EventType.EFFECT_VERIFICATION_FAILED, **info)
                case _:
                    lop.status = str(LogicalOperationStatus.UNKNOWN)
                    c.manager.transition_effect(s, tx, eff, EffectState.UNKNOWN,
                                                f"verification inconclusive: {result.reason}",
                                                event_type=EventType.EFFECT_VERIFICATION_UNKNOWN, **info)
            return result.status
