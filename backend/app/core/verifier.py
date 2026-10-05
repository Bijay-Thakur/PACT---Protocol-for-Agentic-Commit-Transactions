"""Verification engine: did the intended business state actually become true?

Verification re-reads the external system through the adapter's read path; it
never reuses the dispatch response. Polling is bounded; a result that is still
pending (or whose negative evidence is not yet authoritative) becomes UNKNOWN and
goes to reconciliation - never an assumed success, never an assumed failure.

Effect state after verification:
- postcondition MATCH                         -> VERIFIED (causality recorded in ``application``)
- applied, postcondition MISMATCH / PARTIAL   -> FAILED + APPLIED_MISMATCH residual (a real consequence)
- authoritatively not applied                 -> FAILED (NOT_APPLIED_CONFIRMED)
- anything else                               -> UNKNOWN
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import select

from app.core.evidence import execute_attempts, last_dispatch, normalize_negative
from app.core.executor import ExecutionContext
from app.core.work_queue import Claim
from app.domain.effect import EffectView
from app.domain.enums import (
    Application,
    EffectState,
    EventType,
    LogicalOperationStatus,
    ObservationPurpose,
    Postcondition,
)
from app.domain.verification import Observation
from app.persistence.models import LogicalOperationRow
from app.persistence.repositories import append_event, effect_view, load_tree
from app.telemetry.tracing import span


class Verifier:
    def __init__(self, ctx: ExecutionContext):
        self.ctx = ctx

    async def poll(self, view: EffectView, *, restoration: bool = False) -> tuple[Observation, int]:
        adapter = self.ctx.registry.adapter(view.effect_type)
        contract = adapter.contract
        polls, obs = 0, None
        with span("effect.verify", effect_id=view.id, root_id=view.root_id, operation_key=view.operation_key,
                  effect_type=view.effect_type, actor_id=view.actor_id) as sp:
            for i in range(max(1, contract.verification_polls)):
                polls = i + 1
                fn = adapter.observe_restoration if restoration else adapter.observe
                obs = await fn(view, self.ctx.adapter_ctx())
                if obs.readable and not obs.pending:
                    break
                await asyncio.sleep(contract.verification_poll_interval_s)
            sp.set_attribute("pact.postcondition", str(obs.postcondition))
        obs = obs.model_copy(update={"provenance": self.ctx.provenance})
        return obs, polls

    async def verify_effect(self, root_id: UUID, effect_id: UUID, claim: Claim | None,
                            purpose: ObservationPurpose = ObservationPurpose.VERIFY) -> str:
        c = self.ctx
        async with c.db.uow() as s:
            await c.queue.fence(s, claim)
            rows = await load_tree(s, root_id, lock=True)
            eff = rows.effects[effect_id]
            if eff.state == EffectState.DISPATCHED:
                c.manager.transition_effect(s, rows.txs[eff.transaction_id], eff, EffectState.VERIFYING,
                                            "querying external state independently of the dispatch response",
                                            event_type=EventType.VERIFICATION_STARTED)
            view = effect_view(eff)
            attempts = await execute_attempts(s, eff.id)
        obs, polls = await self.poll(view)
        contract = c.registry.contract(view.effect_type)
        ambiguous, sent_at = last_dispatch(attempts)
        obs, negative_auth = normalize_negative(contract, obs, ambiguous=ambiguous, sent_at=sent_at)
        async with c.db.uow() as s:
            await c.queue.fence(s, claim)
            rows = await load_tree(s, root_id, lock=True)
            eff = rows.effects[effect_id]
            tx = rows.txs[eff.transaction_id]
            lop = (await s.execute(select(LogicalOperationRow).where(
                LogicalOperationRow.id == eff.logical_operation_id).with_for_update())).scalar_one()
            row = c.evidence.record_observation(s, eff, obs, purpose, negative_authoritative=negative_auth)
            await s.flush()
            record = {"status": str(obs.verification_status), "application": str(obs.application),
                      "postcondition": str(obs.postcondition), "evidence": obs.evidence, "reason": obs.reason,
                      "external_reference": obs.provider_reference, "observation_id": str(row.id),
                      "polls": polls, "source": "verification", "provider_source": obs.source,
                      "provenance": obs.provenance, "negative_authoritative": negative_auth}
            eff.verification_result = record
            await c.evidence.apply_outcome(s, tx, eff, obs, row)
            if polls > 1:
                append_event(s, tx, EventType.VERIFICATION_PENDING,
                             {"operation_key": eff.operation_key, "polls": polls,
                              "detail": "external state not yet visible; dependents remain blocked"}, effect_id=eff.id)
            info = {"verification": record, "dispatch_outcome": (eff.dispatch_result or {}).get("outcome")}
            if obs.postcondition == Postcondition.MATCH:
                eff.verified_at = c.manager.clock()
                lop.status = str(LogicalOperationStatus.VERIFIED)
                lop.provider_reference = eff.provider_reference
                lop.verified_result = record
                c.manager.transition_effect(s, tx, eff, EffectState.VERIFIED,
                                            "BUSINESS_EFFECT_VERIFIED against external state",
                                            event_type=EventType.EFFECT_VERIFIED, **info)
                return "VERIFIED"
            if obs.application == Application.APPLIED and obs.postcondition in (Postcondition.MISMATCH,
                                                                                 Postcondition.PARTIAL):
                lop.status = str(LogicalOperationStatus.FAILED)
                c.manager.transition_effect(s, tx, eff, EffectState.FAILED,
                                            f"APPLIED but does not match the plan: {obs.reason}",
                                            event_type=EventType.EFFECT_VERIFICATION_FAILED, **info)
                return "APPLIED_MISMATCH"
            if obs.application == Application.NOT_APPLIED_CONFIRMED:
                lop.status = str(LogicalOperationStatus.FAILED)
                c.manager.transition_effect(s, tx, eff, EffectState.FAILED,
                                            f"provider said success but external state shows it was not applied: "
                                            f"{obs.reason}", event_type=EventType.EFFECT_VERIFICATION_FAILED, **info)
                return "NOT_APPLIED"
            lop.status = str(LogicalOperationStatus.UNKNOWN)
            c.manager.transition_effect(s, tx, eff, EffectState.UNKNOWN, f"verification inconclusive: {obs.reason}",
                                        event_type=EventType.EFFECT_VERIFICATION_UNKNOWN, **info)
            return "UNKNOWN"

    async def observe_final(self, view: EffectView) -> tuple[Observation, int]:
        return await self.poll(view)
