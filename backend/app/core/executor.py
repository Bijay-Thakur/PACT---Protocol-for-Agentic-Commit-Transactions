"""Effect executor.

Invokes adapters only for effects that crossed the commit barrier. For every
dispatch it:

1. takes the root aggregate lock and re-checks state, readiness (all dependencies
   VERIFIED) and logical-operation ownership,
2. persists the execution intent (attempt row, effect DISPATCHING, logical op
   IN_FLIGHT) and COMMITS before any external call,
3. calls the adapter with the stable provider idempotency key and PACT ids,
4. classifies the outcome (ACCEPTED / REJECTED / RETRYABLE / RESPONSE_LOST) and
   persists it. A lost response becomes UNKNOWN - never FAILED, never retried.
"""

from __future__ import annotations

from typing import Awaitable, Callable
from uuid import UUID

import httpx
from sqlalchemy import select

from app.adapters.base import AdapterContext
from app.adapters.registry import EffectRegistry
from app.core.effect_graph import EffectGraph
from app.core.transaction_manager import TransactionManager
from app.domain.enums import (
    AttemptKind,
    AttemptStatus,
    DispatchOutcome,
    EffectState,
    EventType,
    LogicalOperationStatus,
    TransactionState,
)
from app.domain.errors import StateConflict
from app.persistence.db import Database
from app.persistence.models import LogicalOperationRow, OperationAttemptRow
from app.persistence.repositories import effect_view, load_tree, next_attempt_no
from app.telemetry.tracing import span

CrashHook = Callable[[str, UUID], Awaitable[None]]


async def _no_crash(point: str, effect_id: UUID) -> None:
    return None


class ExecutionContext:
    """Dependencies shared by executor, verifier, reconciler and compensator."""

    def __init__(self, db: Database, manager: TransactionManager, registry: EffectRegistry,
                 http: httpx.AsyncClient, timeout_s: float, crash_hook: CrashHook | None = None):
        self.db = db
        self.manager = manager
        self.registry = registry
        self.http = http
        self.timeout_s = timeout_s
        self.crash_hook = crash_hook or _no_crash

    def adapter_ctx(self, attempt_no: int = 1) -> AdapterContext:
        return AdapterContext(http=self.http, timeout_s=self.timeout_s, attempt_no=attempt_no)


class Executor:
    def __init__(self, ctx: ExecutionContext):
        self.ctx = ctx

    async def dispatch(self, root_id: UUID, effect_id: UUID) -> DispatchOutcome | None:
        """Dispatch one effect. Returns None if attempts are exhausted (effect FAILED)."""
        c = self.ctx
        async with c.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            root = rows.root
            if root.state != TransactionState.COMMITTING:
                raise StateConflict(f"cannot dispatch while transaction is {root.state}", code="NOT_COMMITTING")
            eff = rows.effects[effect_id]
            tx = rows.txs[eff.transaction_id]
            if eff.state not in (EffectState.PREPARED, EffectState.RETRYABLE):
                raise StateConflict(f"effect is {eff.state}", code="EFFECT_NOT_DISPATCHABLE")
            graph = EffectGraph([e for e in rows.snapshot().effects if e.state != EffectState.ABORTED])
            waiting = graph.unverified_dependencies(eff.id)
            if waiting:
                raise StateConflict("dependencies are not verified", code="DEPENDENCIES_NOT_VERIFIED",
                                    details=[w.operation_key for w in waiting])
            lop = (await s.execute(select(LogicalOperationRow).where(
                LogicalOperationRow.id == eff.logical_operation_id).with_for_update())).scalar_one()
            if lop.owner_root_id != root.id or lop.status not in (LogicalOperationStatus.RESERVED, LogicalOperationStatus.RETRYABLE):
                raise StateConflict(f"logical operation {lop.operation_key} is {lop.status} (owner {lop.owner_root_id})",
                                    code="OPERATION_NOT_OWNED")
            contract = c.registry.contract(eff.contract_type)
            attempt_no = await next_attempt_no(s, lop.id, AttemptKind.EXECUTE)
            if attempt_no > contract.max_attempts:
                c.manager.transition_effect(s, tx, eff, EffectState.FAILED,
                                            f"retry budget exhausted after {contract.max_attempts} attempts")
                lop.status = str(LogicalOperationStatus.FAILED)
                return None
            now = c.manager.clock()
            attempt = OperationAttemptRow(
                logical_operation_id=lop.id, effect_id=eff.id, kind=str(AttemptKind.EXECUTE),
                attempt_no=attempt_no, status=str(AttemptStatus.INTENT_RECORDED),
                request={"operation_key": eff.operation_key, "idempotency_key": lop.provider_idempotency_key,
                         "effect_type": eff.contract_type, "payload": eff.payload,
                         "pact_transaction_id": str(eff.transaction_id), "pact_effect_id": str(eff.id)},
                started_at=now,
            )
            s.add(attempt)
            lop.status = str(LogicalOperationStatus.IN_FLIGHT)
            lop.owner_effect_id = eff.id
            eff.dispatched_at = now
            c.manager.transition_effect(s, tx, eff, EffectState.DISPATCHING, "execution intent persisted",
                                        event_type=EventType.EFFECT_DISPATCH_STARTED, attempt_no=attempt_no,
                                        idempotency_key=lop.provider_idempotency_key)
            await s.flush()
            attempt_id = attempt.id
            view = effect_view(eff)
        # --- intent is durable; only now does anything leave PACT ---
        adapter = c.registry.adapter(view.effect_type)
        with span("effect.dispatch", transaction_id=view.transaction_id, root_id=root_id, effect_id=view.id,
                  operation_key=view.operation_key, actor_id=view.actor_id, effect_type=view.effect_type) as sp:
            result = await adapter.execute(view, c.adapter_ctx(attempt_no))
            sp.set_attribute("pact.dispatch_outcome", str(result.outcome))
        await c.crash_hook("after_external_call", view.id)

        async with c.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            eff = rows.effects[effect_id]
            tx = rows.txs[eff.transaction_id]
            attempt = await s.get(OperationAttemptRow, attempt_id)
            lop = (await s.execute(select(LogicalOperationRow).where(
                LogicalOperationRow.id == eff.logical_operation_id).with_for_update())).scalar_one()
            attempt.status = str(AttemptStatus(result.outcome.value))
            attempt.response = {"http_status": result.http_status, "body": result.response,
                                "latency_ms": result.latency_ms}
            attempt.error = {"message": result.error} if result.error else None
            attempt.finished_at = c.manager.clock()
            eff.dispatch_result = result.model_dump(mode="json")
            info = {"attempt_no": attempt_no, "http_status": result.http_status,
                    "provider_reference": result.provider_reference, "error": result.error,
                    "dispatch_outcome": str(result.outcome)}
            match result.outcome:
                case DispatchOutcome.ACCEPTED:
                    eff.provider_reference = result.provider_reference or eff.provider_reference
                    c.manager.transition_effect(
                        s, tx, eff, EffectState.DISPATCHED,
                        "provider reported success (TOOL_CALL_SUCCEEDED) - not yet verified",
                        event_type=EventType.EFFECT_DISPATCHED, **info)
                case DispatchOutcome.RESPONSE_LOST:
                    lop.status = str(LogicalOperationStatus.UNKNOWN)
                    c.manager.transition_effect(
                        s, tx, eff, EffectState.UNKNOWN,
                        "request may have been applied but no response was received; reconciliation required, blind retry prohibited",
                        event_type=EventType.EFFECT_DISPATCH_UNKNOWN, **info)
                case DispatchOutcome.REJECTED_DEFINITIVE:
                    lop.status = str(LogicalOperationStatus.FAILED)
                    c.manager.transition_effect(s, tx, eff, EffectState.FAILED,
                                                f"provider definitively rejected: {result.error}",
                                                event_type=EventType.EFFECT_DISPATCH_REJECTED, **info)
                case DispatchOutcome.REJECTED_RETRYABLE | DispatchOutcome.NOT_SENT:
                    lop.status = str(LogicalOperationStatus.RETRYABLE)
                    c.manager.transition_effect(s, tx, eff, EffectState.RETRYABLE,
                                                "provider declined before applying; safe to retry with the same idempotency key",
                                                event_type=EventType.EFFECT_DISPATCH_RETRYABLE, **info)
        return result.outcome
