"""Effect executor (Phase 2).

For every dispatch:
1. Under the root aggregate lock and the worker fence: re-check transaction state,
   quarantine, dependency readiness (all VERIFIED), logical-operation ownership,
   the pinned contract version, and *execution authority* (principals active,
   approval still valid). A RETRYABLE effect needs a recorded retry justification.
2. Persist the execution intent (attempt row, effect DISPATCHING, logical op
   IN_FLIGHT) and COMMIT before anything leaves PACT.
3. Call the adapter with the stable provider idempotency key.
4. Persist the outcome under the fence. If the worker was fenced out meanwhile,
   the late response is stored as NON-authoritative evidence and state is untouched.

Outcome mapping is contract-driven: 2xx -> DISPATCHED (application still unknown
until observed); declared definitive rejection -> FAILED/NOT_APPLIED_CONFIRMED;
declared safe-retry status -> RETRYABLE with justification; anything ambiguous ->
UNKNOWN (no retry until reconciliation).
"""

from __future__ import annotations

from typing import Awaitable, Callable
from uuid import UUID

import httpx
from sqlalchemy import select

from app.adapters.base import AdapterContext
from app.adapters.registry import EffectRegistry, UnsupportedContractVersion
from app.core.effect_graph import EffectGraph
from app.core.evidence import EvidenceLedger, execute_attempts
from app.core.transaction_manager import TransactionManager
from app.core.work_queue import Claim, StaleWorker, WorkQueue
from app.domain.enums import (
    Application,
    AttemptKind,
    AttemptStatus,
    DispatchOutcome,
    EffectState,
    EventType,
    LogicalOperationStatus,
    ObservationPurpose,
    TransactionState,
)
from app.domain.errors import StateConflict
from app.domain.verification import Observation
from app.persistence.db import Database
from app.persistence.models import LogicalOperationRow, OperationAttemptRow
from app.persistence.repositories import append_event, effect_view, load_tree, next_attempt_no
from app.telemetry.tracing import span

CrashHook = Callable[[str, UUID], Awaitable[None]]


async def _no_crash(point: str, effect_id: UUID) -> None:
    return None


class AuthorityLost(StateConflict):
    code = "EXECUTION_AUTHORITY_LOST"


class ExecutionContext:
    """Dependencies shared by executor, verifier, reconciler and compensator."""

    def __init__(self, db: Database, manager: TransactionManager, registry: EffectRegistry,
                 http: httpx.AsyncClient, timeout_s: float, queue: WorkQueue, evidence: EvidenceLedger,
                 crash_hook: CrashHook | None = None, provenance: str = "SIMULATED"):
        self.db = db
        self.manager = manager
        self.registry = registry
        self.http = http
        self.timeout_s = timeout_s
        self.queue = queue
        self.evidence = evidence
        self.crash_hook = crash_hook or _no_crash
        self.provenance = provenance

    def adapter_ctx(self, attempt_no: int = 1, attempts: list[OperationAttemptRow] | None = None) -> AdapterContext:
        attempts = attempts or []
        return AdapterContext(http=self.http, timeout_s=self.timeout_s, attempt_no=attempt_no,
                              first_dispatch_at=attempts[0].started_at if attempts else None,
                              last_dispatch_at=attempts[-1].started_at if attempts else None)


class Executor:
    def __init__(self, ctx: ExecutionContext, authority_check: Callable):
        self.ctx = ctx
        self.authority_check = authority_check  # async (session, rows) -> list[str] problems

    async def dispatch(self, root_id: UUID, effect_id: UUID, claim: Claim | None) -> DispatchOutcome | None:
        c = self.ctx
        await c.crash_hook("before_intent", effect_id)
        async with c.db.uow() as s:
            await c.queue.fence(s, claim)
            rows = await load_tree(s, root_id, lock=True)
            root = rows.root
            if root.state != TransactionState.COMMITTING:
                raise StateConflict(f"cannot dispatch while transaction is {root.state}", code="NOT_COMMITTING")
            if root.quarantined:
                raise StateConflict("legacy transaction is quarantined from automatic execution", code="QUARANTINED")
            eff = rows.effects[effect_id]
            tx = rows.txs[eff.transaction_id]
            if eff.state not in (EffectState.PREPARED, EffectState.RETRYABLE):
                raise StateConflict(f"effect is {eff.state}", code="EFFECT_NOT_DISPATCHABLE")
            graph = EffectGraph([e for e in rows.snapshot().effects if e.state != EffectState.ABORTED])
            waiting = graph.unverified_dependencies(eff.id)
            if waiting:
                raise StateConflict("dependencies are not verified", code="DEPENDENCIES_NOT_VERIFIED",
                                    details=[w.operation_key for w in waiting])
            c.registry.pinned(eff.contract_type, eff.contract_version, eff.contract_hash)  # may raise: held
            problems = await self.authority_check(s, rows)
            if problems:
                raise AuthorityLost("execution authority no longer valid: " + "; ".join(problems),
                                    details={"problems": problems})
            lop = (await s.execute(select(LogicalOperationRow).where(
                LogicalOperationRow.id == eff.logical_operation_id).with_for_update())).scalar_one()
            if lop.owner_root_id != root.id or lop.status not in (LogicalOperationStatus.RESERVED,
                                                                  LogicalOperationStatus.RETRYABLE):
                raise StateConflict(f"logical operation {lop.operation_key} is {lop.status} (owner {lop.owner_root_id})",
                                    code="OPERATION_NOT_OWNED")
            contract = c.registry.contract(eff.contract_type)
            attempts = await execute_attempts(s, eff.id)
            justification = None
            if eff.state == EffectState.RETRYABLE:
                justification = (eff.reconciliation_result or {}).get("retry_justification") or \
                    (eff.dispatch_result or {}).get("retry_justification")
                if not justification:
                    raise StateConflict("retry without a recorded contract justification is prohibited",
                                        code="RETRY_NOT_JUSTIFIED")
            attempt_no = await next_attempt_no(s, lop.id, AttemptKind.EXECUTE)
            if attempt_no > contract.max_attempts:
                c.manager.transition_effect(s, tx, eff, EffectState.FAILED,
                                            f"retry budget exhausted after {contract.max_attempts} attempts")
                lop.status = str(LogicalOperationStatus.FAILED)
                return None
            now = c.manager.clock()
            attempt = OperationAttemptRow(
                logical_operation_id=lop.id, effect_id=eff.id, kind=str(AttemptKind.EXECUTE), attempt_no=attempt_no,
                status=str(AttemptStatus.INTENT_RECORDED), started_at=now,
                work_item_id=claim.item_id if claim else None, worker_epoch=claim.epoch if claim else None,
                request={"operation_key": eff.operation_key, "idempotency_key": lop.provider_idempotency_key,
                         "effect_type": eff.contract_type, "contract_version": eff.contract_version,
                         "payload": eff.payload, "retry_justification": justification,
                         "pact_transaction_id": str(eff.transaction_id), "pact_effect_id": str(eff.id)},
            )
            s.add(attempt)
            lop.status = str(LogicalOperationStatus.IN_FLIGHT)
            lop.owner_effect_id = eff.id
            eff.dispatched_at = now
            eff.application = str(Application.UNKNOWN)  # it is leaving PACT: from here on, never "not sent"
            c.manager.transition_effect(s, tx, eff, EffectState.DISPATCHING, "execution intent persisted",
                                        event_type=EventType.EFFECT_DISPATCH_STARTED, attempt_no=attempt_no,
                                        idempotency_key=lop.provider_idempotency_key, retry_justification=justification)
            await s.flush()
            attempt_id = attempt.id
            view = effect_view(eff)
        await c.crash_hook("after_intent", effect_id)
        adapter = c.registry.adapter(view.effect_type)
        with span("effect.dispatch", transaction_id=view.transaction_id, root_id=root_id, effect_id=view.id,
                  operation_key=view.operation_key, actor_id=view.actor_id, effect_type=view.effect_type) as sp:
            result = await adapter.execute(view, c.adapter_ctx(attempt_no, attempts))
            sp.set_attribute("pact.dispatch_outcome", str(result.outcome))
        await c.crash_hook("after_external_call", view.id)

        try:
            async with c.db.uow() as s:
                await c.queue.fence(s, claim)
                await self._record_result(s, root_id, effect_id, attempt_id, attempt_no, result, contract)
        except StaleWorker:
            await self._record_late(root_id, effect_id, attempt_id, result)
            raise
        return result.outcome

    async def _record_result(self, s, root_id, effect_id, attempt_id, attempt_no, result, contract) -> None:
        c = self.ctx
        rows = await load_tree(s, root_id, lock=True)
        eff = rows.effects[effect_id]
        tx = rows.txs[eff.transaction_id]
        attempt = await s.get(OperationAttemptRow, attempt_id)
        lop = (await s.execute(select(LogicalOperationRow).where(
            LogicalOperationRow.id == eff.logical_operation_id).with_for_update())).scalar_one()
        attempt.status = str(AttemptStatus(result.outcome.value))
        attempt.response = {"http_status": result.http_status, "body": result.response, "latency_ms": result.latency_ms}
        attempt.error = {"message": result.error} if result.error else None
        attempt.finished_at = c.manager.clock()
        record = result.model_dump(mode="json")
        info = {"attempt_no": attempt_no, "http_status": result.http_status, "error": result.error,
                "provider_reference": result.provider_reference, "dispatch_outcome": str(result.outcome)}
        match result.outcome:
            case DispatchOutcome.ACCEPTED:
                eff.provider_reference = result.provider_reference or eff.provider_reference
                eff.dispatch_result = record
                c.manager.transition_effect(
                    s, tx, eff, EffectState.DISPATCHED,
                    "provider reported success (TOOL_CALL_SUCCEEDED) - application not yet evidenced",
                    event_type=EventType.EFFECT_DISPATCHED, **info)
            case DispatchOutcome.RESPONSE_LOST:
                eff.dispatch_result = record
                lop.status = str(LogicalOperationStatus.UNKNOWN)
                obs = Observation(application=Application.UNKNOWN, postcondition=eff.postcondition,
                                  reason=f"ambiguous dispatch: {result.error or result.http_status}")
                await c.evidence.apply_outcome(s, tx, eff, obs, None)
                c.manager.transition_effect(
                    s, tx, eff, EffectState.UNKNOWN,
                    "request may have been applied; reconciliation required, blind retry prohibited",
                    event_type=EventType.EFFECT_DISPATCH_UNKNOWN, **info)
            case DispatchOutcome.REJECTED_DEFINITIVE:
                eff.dispatch_result = record
                lop.status = str(LogicalOperationStatus.FAILED)
                eff.application = str(Application.NOT_APPLIED_CONFIRMED)
                await c.evidence.release_unsent(s, eff, f"provider definitively rejected ({result.http_status})")
                c.manager.transition_effect(s, tx, eff, EffectState.FAILED,
                                            f"provider definitively rejected: {result.error}",
                                            event_type=EventType.EFFECT_DISPATCH_REJECTED, **info)
            case DispatchOutcome.REJECTED_RETRYABLE | DispatchOutcome.NOT_SENT:
                why = ("TRANSPORT_NOT_SENT: the request never left PACT" if result.outcome == DispatchOutcome.NOT_SENT
                       else contract.safe_retry_statuses.get(result.http_status or 0))
                eff.dispatch_result = {**record, "retry_justification": why}
                lop.status = str(LogicalOperationStatus.RETRYABLE)
                c.manager.transition_effect(s, tx, eff, EffectState.RETRYABLE, f"retry permitted: {why}",
                                            event_type=EventType.EFFECT_DISPATCH_RETRYABLE, retry_justification=why,
                                            **info)

    async def _record_late(self, root_id: UUID, effect_id: UUID, attempt_id: UUID, result) -> None:
        """A fenced-out worker's provider response: keep it as non-authoritative evidence only."""
        c = self.ctx
        async with c.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            eff = rows.effects[effect_id]
            attempt = await s.get(OperationAttemptRow, attempt_id)
            if attempt is not None and attempt.finished_at is None:
                attempt.authoritative = False
                attempt.status = "STALE_LATE_RESPONSE"
                attempt.response = {"late_outcome": str(result.outcome), "http_status": result.http_status,
                                    "body": result.response}
                attempt.finished_at = c.manager.clock()
            obs = Observation(application=Application.UNKNOWN, postcondition=eff.postcondition,
                              evidence={"late_dispatch_outcome": str(result.outcome),
                                        "http_status": result.http_status, "body": result.response},
                              reason="late response from a fenced-out worker (non-authoritative)",
                              source="late-response", provenance=c.provenance)
            c.evidence.record_observation(s, eff, obs, ObservationPurpose.LATE_RESPONSE, attempt_id=attempt_id,
                                          authoritative=False)
            append_event(s, rows.root, EventType.STALE_WORKER_FENCED, {
                "operation_key": eff.operation_key, "late_dispatch_outcome": str(result.outcome),
                "note": "stored as evidence for reconciliation; state not changed"}, effect_id=effect_id)


__all__ = ["Executor", "ExecutionContext", "CrashHook", "AuthorityLost", "UnsupportedContractVersion"]
