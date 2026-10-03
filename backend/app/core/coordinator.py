"""Transaction coordinator: prepare -> barrier -> commit -> verify -> recover -> receipt.

Each stage is an explicit method; nothing is collapsed into a generic
"run the tools" loop. Execution walks the effect DAG in deterministic
topological order, one effect at a time, and an effect is dispatched only when
all of its dependencies are VERIFIED against external state.

Child transactions mirror the root's global phase after the barrier (they
cannot commit independently); their per-effect detail stays on the effects.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.commit_barrier import CommitBarrier
from app.core.compensator import Compensator
from app.core.effect_graph import EffectGraph
from app.core.executor import ExecutionContext, Executor
from app.core.invariant_engine import InvariantEngine
from app.core.receipt_generator import ReceiptGenerator
from app.core.reconciler import Reconciler
from app.core.state_machine import TERMINAL_TX_STATES, TX_TRANSITIONS
from app.core.transaction_manager import SPECIFIABLE, TransactionManager
from app.core.verifier import Verifier
from app.domain.decision import CommitDecision
from app.domain.enums import (
    AttemptStatus,
    EffectState,
    EventType,
    FailureAction,
    InvariantPhase,
    LogicalOperationStatus,
    OperatorActionType,
    TransactionState,
    VerificationStatus,
)
from app.domain.errors import StateConflict, ValidationFailed
from app.domain.invariant import InvariantEvaluation
from app.domain.transaction import RecoveryPolicy
from app.persistence.models import (
    CommitDecisionRow,
    EffectDependencyRow,
    InvariantEvaluationRow,
    OperationAttemptRow,
    TransactionRow,
)
from app.persistence.repositories import TreeRows, append_event, effect_view, get_tx, load_tree
from app.telemetry.tracing import span

T = TransactionState
E = EffectState

# Intermediate hops used when moving a whole tree to a target phase.
_PATHS: dict[tuple[T, T], list[T]] = {
    (T.CREATED, T.ABORTED): [T.ABORTING, T.ABORTED],
    (T.SPECIFYING, T.ABORTED): [T.ABORTING, T.ABORTED],
    (T.PREPARING, T.ABORTED): [T.ABORTING, T.ABORTED],
    (T.PREPARED, T.ABORTED): [T.ABORTING, T.ABORTED],
}


@dataclass
class CommitOutcome:
    decision: CommitDecision
    state: str


class Coordinator:
    def __init__(self, ctx: ExecutionContext, manager: TransactionManager, barrier: CommitBarrier,
                 invariants: InvariantEngine, receipts: ReceiptGenerator):
        self.ctx = ctx
        self.db = ctx.db
        self.manager = manager
        self.barrier = barrier
        self.invariants = invariants
        self.receipts = receipts
        self.executor = Executor(ctx)
        self.verifier = Verifier(ctx)
        self.reconciler = Reconciler(ctx, self.verifier)
        self.compensator = Compensator(ctx, self.verifier)
        self._tasks: set[asyncio.Task] = set()

    # ================================================================ helpers
    def _move_tree(self, s: AsyncSession, rows: TreeRows, to: T, reason: str, **payload: Any) -> None:
        """Move root and every non-terminal descendant to ``to`` via legal hops."""
        for tx in rows.ordered_txs():
            cur = T(tx.state)
            if cur == to or cur in TERMINAL_TX_STATES:
                continue
            hops = _PATHS.get((cur, to)) or ([to] if to in TX_TRANSITIONS[cur] else None)
            if hops is None:
                if tx.id == rows.root.id:
                    raise StateConflict(f"root cannot move {cur} -> {to}", code="INVALID_TREE_TRANSITION")
                continue  # a child already past this phase (e.g. aborted optional child)
            for hop in hops:
                self.manager.transition_tx(s, tx, hop, reason, **payload)

    def _persist_evaluations(self, s: AsyncSession, rows: TreeRows, evals: list[InvariantEvaluation], context: str) -> None:
        for ev in evals:
            inv = next(i for i in rows.invariants if i.id == ev.invariant_id)
            s.add(InvariantEvaluationRow(invariant_id=inv.id, transaction_id=inv.transaction_id, phase=str(ev.phase),
                                         context=context, passed=ev.passed, observed_values=ev.observed_values,
                                         reason=ev.reason))
            append_event(s, rows.txs[inv.transaction_id], EventType.INVARIANT_EVALUATED, {
                "key": ev.invariant_key, "name": ev.name, "phase": str(ev.phase), "context": context,
                "passed": ev.passed, "reason": ev.reason, "failure_action": str(ev.failure_action),
                "observed": ev.observed_values,
            })

    def _policy(self, rows: TreeRows) -> RecoveryPolicy:
        return RecoveryPolicy(**(rows.root.policy or {}))

    async def _root_id(self, tx_id: UUID) -> UUID:
        async with self.db.read() as s:
            return (await get_tx(s, tx_id)).root_id

    async def finalize(self, root_id: UUID) -> None:
        """Emit receipts for every terminal transaction in the tree (children first)."""
        async with self.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            for tx in sorted(rows.ordered_txs(), key=lambda t: -t.depth):
                await self.receipts.finalize(s, rows, tx.id)

    # ================================================================ prepare
    async def prepare(self, tx_id: UUID) -> str:
        """Prepare a transaction and (first) any unprepared descendants."""
        root_id = await self._root_id(tx_id)
        async with self.db.read() as s:
            rows = await load_tree(s, root_id, lock=False)
            sub = rows.snapshot().subtree_ids(tx_id)
            order = [t.id for t in sorted(rows.ordered_txs(), key=lambda t: -t.depth) if t.id in sub]
        for tid in order:
            await self._prepare_one(root_id, tid)
        await self.finalize(root_id)
        async with self.db.read() as s:
            return (await get_tx(s, tx_id)).state

    async def _prepare_one(self, root_id: UUID, tx_id: UUID) -> None:
        with span("transaction.prepare", transaction_id=tx_id, root_id=root_id):
            async with self.db.uow() as s:
                rows = await load_tree(s, root_id, lock=True)
                tx = rows.txs[tx_id]
                if T(tx.state) not in SPECIFIABLE:
                    return  # already prepared / aborted / beyond
                if tx.state == T.CREATED:
                    self.manager.transition_tx(s, tx, T.SPECIFYING, "specification closed by prepare")
                self.manager.transition_tx(s, tx, T.PREPARING, "evaluating local authority and preconditions")
                snap = rows.snapshot()
                cap = snap.cap_of(tx_id)
                now = self.manager.clock()
                local: dict[UUID, list[str]] = {}
                for e in snap.effects_in({tx_id}):
                    if e.state != E.VALIDATED:
                        continue
                    violations = self.ctx.manager.authority.check_effect(cap, e, now)
                    local[e.id] = [f"{v.code}: {v.detail}" for v in violations]
                    append_event(s, tx, EventType.AUTHORITY_EVALUATED, {
                        "operation_key": e.operation_key, "passed": not violations,
                        "violations": [v.model_dump() for v in violations], "scope": "local (child capability)",
                    }, effect_id=e.id)
                views = {eid: effect_view(rows.effects[eid]) for eid in local}

            # Read-only precondition checks against external systems (no locks held).
            prepared: dict[UUID, Any] = {}
            for eid, view in views.items():
                if local[eid]:
                    continue
                prepared[eid] = await self.ctx.registry.adapter(view.effect_type).prepare(view, self.ctx.adapter_ctx())

            async with self.db.uow() as s:
                rows = await load_tree(s, root_id, lock=True)
                tx = rows.txs[tx_id]
                failures: list[str] = []
                for eid in sorted(views, key=lambda i: views[i].operation_key):
                    eff = rows.effects[eid]
                    reasons = list(local[eid])
                    result = prepared.get(eid)
                    if result is not None and not result.ok:
                        reasons.append(f"PRECONDITION_FAILED: {result.reason}")
                    if reasons:
                        failures.append(f"{eff.operation_key}: {'; '.join(reasons)}")
                        append_event(s, tx, EventType.EFFECT_PREPARE_FAILED,
                                     {"operation_key": eff.operation_key, "reasons": reasons}, effect_id=eid)
                        continue
                    eff.prepare_evidence = result.observations
                    self.manager.transition_effect(s, tx, eff, E.PREPARED, "authority valid, preconditions hold",
                                                   event_type=EventType.EFFECT_PREPARED,
                                                   observations=result.observations)
                await s.flush()
                snap = rows.snapshot()
                graph = EffectGraph(snap.effects)
                evals = [] if graph.find_cycles() else self.invariants.evaluate(
                    snap, graph, InvariantPhase.PREPARE, self.manager.clock(), scope_tx_id=tx_id)
                self._persist_evaluations(s, rows, evals, "prepare")
                failures += [f"INVARIANT_FAILED:{ev.invariant_key}: {ev.reason}" for ev in evals
                             if not ev.passed and ev.failure_action in (FailureAction.BLOCK_PREPARE, FailureAction.ABORT)]
                if failures:
                    self.manager.transition_tx(s, tx, T.ABORTING, "prepare failed", failures=failures)
                    self.manager.transition_tx(s, tx, T.ABORTED, "prepare failed; no effect was dispatched")
                    for eff in rows.effects_of(tx_id):
                        if E(eff.state) in (E.PROPOSED, E.VALIDATED, E.PREPARED):
                            self.manager.transition_effect(s, tx, eff, E.ABORTED, "transaction aborted during prepare",
                                                           event_type=EventType.EFFECT_RELEASED)
                    if tx.id == root_id:
                        self._abort_tree_rows(s, rows, "root prepare failed")
                else:
                    self.manager.transition_tx(s, tx, T.PREPARED, "locally prepared",
                                               effects=[e.operation_key for e in rows.effects_of(tx_id)])

    def _abort_tree_rows(self, s: AsyncSession, rows: TreeRows, reason: str) -> None:
        self._move_tree(s, rows, T.ABORTED, reason)
        for eff in rows.effects.values():
            if E(eff.state) in (E.PROPOSED, E.VALIDATED, E.PREPARED, E.RETRYABLE):
                self.manager.transition_effect(s, rows.txs[eff.transaction_id], eff, E.ABORTED, reason,
                                               event_type=EventType.EFFECT_RELEASED)

    # ========================================================= commit barrier
    async def evaluate_barrier(self, root_id: UUID) -> CommitDecision:
        """Dry-run barrier evaluation (not persisted, not binding)."""
        async with self.db.read() as s:
            rows = await load_tree(s, root_id, lock=False)
            result = self.barrier.evaluate(rows.snapshot(), self.manager.clock(), binding=False)
            return result.decision

    async def commit(self, tx_id: UUID, *, step_delay_s: float = 0.0, background: bool = False) -> CommitOutcome:
        async with self.db.read() as s:
            tx = await get_tx(s, tx_id)
            if tx.parent_id is not None:
                raise StateConflict(
                    "child transactions cannot commit independently; only the root may cross the commit barrier",
                    code="CHILD_CANNOT_COMMIT_INDEPENDENTLY")
        root_id = tx_id
        with span("commit_barrier.evaluate", transaction_id=root_id, root_id=root_id) as sp:
            async with self.db.uow() as s:
                rows = await load_tree(s, root_id, lock=True, lock_operations=True)
                root = rows.root
                if root.state != T.PREPARED:
                    code = "COMMIT_ALREADY_IN_PROGRESS" if root.state in (T.COMMITTING, T.VERIFYING) else "COMMIT_NOT_PERMITTED"
                    raise StateConflict(f"cannot commit a transaction in state {root.state}", code=code,
                                        details={"state": root.state})
                result = self.barrier.evaluate(rows.snapshot(), self.manager.clock(), binding=True)
                d = result.decision
                sp.set_attribute("pact.eligible", d.eligible)
                s.add(CommitDecisionRow(transaction_id=root_id, eligible=d.eligible, snapshot_version=d.snapshot_version,
                                        checks=[c.model_dump(mode="json") for c in d.checks],
                                        blocking_reasons=d.blocking_reasons, evaluated_at=d.evaluated_at))
                self._persist_evaluations(s, rows, result.invariant_evaluations, "commit_barrier")
                append_event(s, root, EventType.COMMIT_BARRIER_EVALUATED, {
                    "eligible": d.eligible, "blocking_reasons": d.blocking_reasons,
                    "checks": [{"code": c.code, "subject": c.subject, "passed": c.passed} for c in d.checks],
                })
                if not d.eligible:
                    self._abort_tree_rows(s, rows, "commit barrier rejected: " + ", ".join(d.blocking_reasons))
                else:
                    executable = set(result.executable_effect_ids)
                    for eff in rows.effects.values():
                        if eff.id not in executable and E(eff.state) not in (E.ABORTED, E.FAILED):
                            self.manager.transition_effect(s, rows.txs[eff.transaction_id], eff, E.ABORTED,
                                                           "excluded optional child", event_type=EventType.EFFECT_RELEASED)
                    for tx in rows.ordered_txs():
                        if tx.id != root_id and not tx.required and tx.state != T.PREPARED:
                            for hop in _PATHS.get((T(tx.state), T.ABORTED), []):
                                self.manager.transition_tx(s, tx, hop, "optional child excluded at commit")
                    reserved = []
                    for eid in sorted(executable, key=lambda i: rows.effects[i].operation_key):
                        eff = rows.effects[eid]
                        lop = rows.logical_ops[eff.logical_operation_id]
                        lop.status = str(LogicalOperationStatus.RESERVED)
                        lop.owner_root_id = root_id
                        lop.owner_effect_id = eff.id
                        reserved.append(lop.operation_key)
                    graph = EffectGraph([n for n in rows.snapshot().effects if n.id in executable])
                    s.add_all([EffectDependencyRow(from_effect_id=a, to_effect_id=b) for a, b in graph.edges()])
                    append_event(s, root, EventType.LOGICAL_OPERATIONS_RESERVED, {
                        "operation_keys": reserved,
                        "execution_levels": {rows.effects[k].operation_key: v for k, v in graph.levels().items()},
                    })
                    self._move_tree(s, rows, T.COMMITTING, "global commit barrier passed")
        if not d.eligible:
            await self.finalize(root_id)
            return CommitOutcome(decision=d, state=T.ABORTED)
        if background:
            self._spawn(self.drive(root_id, step_delay_s=step_delay_s))
            return CommitOutcome(decision=d, state=T.COMMITTING)
        state = await self.drive(root_id, step_delay_s=step_delay_s)
        return CommitOutcome(decision=d, state=state)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_background(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ============================================================== execution
    async def drive(self, root_id: UUID, *, step_delay_s: float = 0.0) -> str:
        """Walk the DAG until terminal, UNKNOWN, HUMAN_REQUIRED, or recovery."""
        rounds = 0
        while True:
            async with self.db.uow() as s:
                rows = await load_tree(s, root_id, lock=True)
                if rows.root.state != T.COMMITTING:
                    return rows.root.state
                snap = rows.snapshot()
                live = [e for e in snap.effects if e.state not in (E.ABORTED,)]
                graph = EffectGraph(live)
                failed = [e for e in live if e.state == E.FAILED]
                unresolved = [e for e in live if e.state in (E.UNKNOWN, E.RECONCILING, E.HUMAN_REQUIRED)]
                in_flight = [e for e in live if e.state in (E.DISPATCHING, E.DISPATCHED, E.VERIFYING)]
                pending = [e for e in graph.topological_order() if e.state in (E.PREPARED, E.RETRYABLE)]
                if in_flight:
                    # Another worker owns an in-flight effect; never act on it concurrently.
                    return rows.root.state
                if not failed and unresolved:
                    self._move_tree(s, rows, T.UNKNOWN, "effect outcome is UNKNOWN; execution halted (no blind retry)",
                                    unknown=[e.operation_key for e in unresolved])
                    policy = self._policy(rows)
                    auto = policy.on_unknown == "RECONCILE" and policy.auto_reconcile and rounds < policy.max_auto_reconcile_rounds
                elif not failed and pending:
                    ready = [e for e in pending if graph.is_ready(e.id)]
                    waiting = {e.operation_key: [d.operation_key + f" ({d.state})" for d in graph.unverified_dependencies(e.id)]
                               for e in pending if not graph.is_ready(e.id)}
                    append_event(s, rows.root, EventType.EXECUTION_SCHEDULE_EVALUATED, {
                        "next": ready[0].operation_key if ready else None,
                        "ready": [e.operation_key for e in ready], "waiting_on_verification": waiting,
                    })
            if failed:
                return await self._recover_from_failure(root_id)
            if unresolved:
                if auto:
                    rounds += 1
                    if await self.reconcile(root_id, resume=False) == T.COMMITTING:
                        continue
                    return await self._state(root_id)
                return T.UNKNOWN
            if not pending:
                return await self._final_verification(root_id)
            if not ready:
                async with self.db.uow() as s:
                    rows = await load_tree(s, root_id, lock=True)
                    self._move_tree(s, rows, T.HUMAN_REQUIRED, "no effect is ready and none is in flight",
                                    waiting=waiting)
                return T.HUMAN_REQUIRED
            nxt = ready[0]
            outcome = await self.executor.dispatch(root_id, nxt.id)
            if step_delay_s:
                await asyncio.sleep(step_delay_s)
            if outcome is not None and outcome.value == "ACCEPTED":
                await self.verifier.verify_effect(root_id, nxt.id)
                if step_delay_s:
                    await asyncio.sleep(step_delay_s)
            elif outcome is not None and outcome.value in ("REJECTED_RETRYABLE", "NOT_SENT"):
                await asyncio.sleep(0.05)

    # ============================================================== recovery
    async def _recover_from_failure(self, root_id: UUID) -> str:
        with span("transaction.recovery", transaction_id=root_id, root_id=root_id):
            async with self.db.uow() as s:
                rows = await load_tree(s, root_id, lock=True)
                snap = rows.snapshot()
                graph = EffectGraph([e for e in snap.effects if e.state != E.ABORTED])
                evals = self.invariants.evaluate(snap, graph, InvariantPhase.POST_EXECUTION, self.manager.clock())
                self._persist_evaluations(s, rows, evals, "recovery")
                policy = self._policy(rows)
                failing = [ev for ev in evals if not ev.passed]
                human = policy.on_effect_failure == "HUMAN_REQUIRED" or any(
                    ev.failure_action == FailureAction.HUMAN_REQUIRED for ev in failing)
                action = "HUMAN_REQUIRED" if human else "COMPENSATE"
                failed = [{"operation_key": e.operation_key, "effect_type": e.contract_type,
                           "dispatch": (e.dispatch_result or {}).get("outcome"),
                           "verification": (e.verification_result or {}).get("status"),
                           "error": (e.dispatch_result or {}).get("error")}
                          for e in rows.effects.values() if e.state == E.FAILED]
                released = []
                for eff in rows.effects.values():
                    if E(eff.state) in (E.PREPARED, E.RETRYABLE):
                        lop = rows.logical_ops[eff.logical_operation_id]
                        if lop.owner_root_id == root_id:
                            lop.status = str(LogicalOperationStatus.AVAILABLE)
                            lop.owner_root_id = None
                            lop.owner_effect_id = None
                        self.manager.transition_effect(s, rows.txs[eff.transaction_id], eff, E.ABORTED,
                                                       "never dispatched; released by recovery",
                                                       event_type=EventType.EFFECT_RELEASED)
                        released.append(eff.operation_key)
                append_event(s, rows.root, EventType.RECOVERY_DECISION, {
                    "failed_effects": failed, "action": action, "released_effects": released,
                    "policy": policy.model_dump(),
                    "invariants": [{"key": ev.invariant_key, "passed": ev.passed, "reason": ev.reason,
                                    "failure_action": str(ev.failure_action)} for ev in evals],
                })
                if action == "COMPENSATE":
                    self._move_tree(s, rows, T.COMPENSATING, "definitive effect failure; compensating committed effects")
                else:
                    self._move_tree(s, rows, T.HUMAN_REQUIRED, "recovery policy requires a human")
            if action == "COMPENSATE":
                return await self._run_compensation(root_id)
            return T.HUMAN_REQUIRED

    async def _run_compensation(self, root_id: UUID, *, retry_failed: bool = False) -> str:
        report = await self.compensator.run(root_id, retry_failed=retry_failed)
        async with self.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            append_event(s, rows.root, EventType.COMPENSATION_FINISHED, {
                "compensated": report.compensated, "failed": report.failed,
                "irreversible_residue": report.irreversible, "complete": report.complete,
            })
            if report.complete:
                self._move_tree(s, rows, T.COMPENSATED, "all committed effects compensated and verified")
            else:
                self._move_tree(s, rows, T.HUMAN_REQUIRED,
                                "restitution incomplete: compensation failed or irreversible effects remain",
                                failed=report.failed, irreversible=report.irreversible)
            state = rows.root.state
        await self.finalize(root_id)
        return state

    async def _final_verification(self, root_id: UUID) -> str:
        with span("transaction.final_verification", transaction_id=root_id, root_id=root_id):
            async with self.db.uow() as s:
                rows = await load_tree(s, root_id, lock=True)
                if rows.root.state == T.COMMITTING:
                    self._move_tree(s, rows, T.VERIFYING, "all effects verified; final transaction-level verification")
                append_event(s, rows.root, EventType.FINAL_VERIFICATION_STARTED, {})
                views = [effect_view(e) for e in rows.effects.values() if e.state == E.VERIFIED]
            drift = []
            observations = {}
            for v in sorted(views, key=lambda v: v.operation_key):
                res, _ = await self.verifier.observe(v)
                observations[v.operation_key] = {"status": str(res.status), "evidence": res.evidence}
                if res.status != VerificationStatus.VERIFIED_SUCCESS:
                    drift.append(v.operation_key)
            async with self.db.uow() as s:
                rows = await load_tree(s, root_id, lock=True)
                snap = rows.snapshot()
                graph = EffectGraph([e for e in snap.effects if e.state != E.ABORTED])
                evals = []
                for phase in (InvariantPhase.POST_EXECUTION, InvariantPhase.FINAL):
                    evals += self.invariants.evaluate(snap, graph, phase, self.manager.clock())
                self._persist_evaluations(s, rows, evals, "final_verification")
                failing = [ev for ev in evals if not ev.passed]
                append_event(s, rows.root, EventType.FINAL_VERIFICATION_FINISHED, {
                    "re_verification": observations, "drift": drift,
                    "invariants": [{"key": ev.invariant_key, "passed": ev.passed} for ev in evals],
                })
                if not failing and not drift:
                    self._move_tree(s, rows, T.COMMITTED_VERIFIED, "every effect verified; final invariants hold")
                    nxt = T.COMMITTED_VERIFIED
                elif not drift and all(ev.failure_action == FailureAction.COMPENSATE for ev in failing):
                    self._move_tree(s, rows, T.COMPENSATING, "final invariant failed; compensating",
                                    failing=[ev.invariant_key for ev in failing])
                    nxt = T.COMPENSATING
                else:
                    self._move_tree(s, rows, T.HUMAN_REQUIRED, "final verification failed",
                                    drift=drift, failing=[ev.invariant_key for ev in failing])
                    nxt = T.HUMAN_REQUIRED
        if nxt == T.COMPENSATING:
            return await self._run_compensation(root_id)
        await self.finalize(root_id)
        return nxt

    # ========================================================= reconciliation
    async def reconcile(self, root_id: UUID, *, resume: bool = True) -> str:
        """Reconcile UNKNOWN effects; on resolution resume ordered execution (if ``resume``)."""
        async with self.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            if rows.root.parent_id is not None:
                raise StateConflict("reconcile the root transaction", code="NOT_A_ROOT_TRANSACTION")
            if rows.root.state not in (T.UNKNOWN, T.HUMAN_REQUIRED):
                raise StateConflict(f"nothing to reconcile in state {rows.root.state}", code="NOTHING_TO_RECONCILE")
            targets = sorted(
                (e for e in rows.effects.values()
                 if e.state == E.UNKNOWN or (e.state == E.HUMAN_REQUIRED and e.compensation_result is None)),
                key=lambda e: e.operation_key)
            if not targets:
                raise StateConflict("no UNKNOWN effects to reconcile", code="NOTHING_TO_RECONCILE")
            self._move_tree(s, rows, T.RECONCILING, "reconciling UNKNOWN outcomes against external state",
                            effects=[e.operation_key for e in targets])
            target_ids = [e.id for e in targets]
        with span("transaction.reconcile", transaction_id=root_id, root_id=root_id):
            outcomes = {}
            for eid in target_ids:
                outcomes[str(eid)] = str(await self.reconciler.reconcile_effect(root_id, eid))
        async with self.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            states = {E(e.state) for e in rows.effects.values()}
            append_event(s, rows.root, EventType.RECONCILIATION_FINISHED, {
                "outcomes": {rows.effects[UUID(k)].operation_key: v for k, v in outcomes.items()}})
            if E.HUMAN_REQUIRED in states:
                self._move_tree(s, rows, T.HUMAN_REQUIRED, "reconciliation requires a human decision")
                return T.HUMAN_REQUIRED
            if E.UNKNOWN in states:
                self._move_tree(s, rows, T.UNKNOWN, "outcome still unknown after reconciliation")
                return T.UNKNOWN
            self._move_tree(s, rows, T.COMMITTING, "ambiguity resolved; resuming ordered execution")
        return await self.drive(root_id) if resume else T.COMMITTING

    # ======================================================= operator actions
    async def operator_action(self, root_id: UUID, operator_id: str, action: OperatorActionType,
                              note: str, effect_id: UUID | None = None) -> str:
        if action == OperatorActionType.APPROVE:
            await self.manager.approve(root_id, operator_id, note)
            return (await self._state(root_id))
        async with self.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            if rows.root.state != T.HUMAN_REQUIRED:
                raise StateConflict(f"operator action {action} requires HUMAN_REQUIRED (is {rows.root.state})",
                                    code="NOT_AWAITING_OPERATOR")
            await self.manager.record_operator_action(s, rows.root, operator_id, action, note, effect_id)
            if action == OperatorActionType.MARK_EFFECT_COMPENSATED:
                eff = rows.effects.get(effect_id) if effect_id else None
                if eff is None or eff.state != E.HUMAN_REQUIRED:
                    raise ValidationFailed("effect_id must reference an effect in HUMAN_REQUIRED", code="INVALID_EFFECT")
                self.manager.transition_effect(s, rows.txs[eff.transaction_id], eff, E.COMPENSATED,
                                               f"operator {operator_id} attested manual restitution: {note}",
                                               actor=f"operator:{operator_id}")
                unresolved = [e for e in rows.effects.values() if e.state == E.HUMAN_REQUIRED and e.id != eff.id]
                if not unresolved and not await self._irreversible_residue(rows):
                    self._move_tree(s, rows, T.COMPENSATING, "operator resolved remaining restitution")
                    self._move_tree(s, rows, T.COMPENSATED, "all committed effects restituted",
                                    actor=f"operator:{operator_id}")
            elif action == OperatorActionType.FINALIZE_FAILED:
                self._move_tree(s, rows, T.FAILED_TERMINAL, f"finalized by operator {operator_id}: {note}",
                                actor=f"operator:{operator_id}")
        if action == OperatorActionType.RETRY_RECONCILIATION:
            return await self.reconcile(root_id)
        if action == OperatorActionType.RETRY_COMPENSATION:
            return await self.compensate(root_id)
        await self.finalize(root_id)
        return await self._state(root_id)

    async def _irreversible_residue(self, rows: TreeRows) -> bool:
        return any(e.state == E.VERIFIED and not self.ctx.registry.contract(e.contract_type).compensable
                   for e in rows.effects.values())

    async def compensate(self, root_id: UUID) -> str:
        """Operator-initiated (re)compensation from HUMAN_REQUIRED."""
        async with self.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            if rows.root.state != T.HUMAN_REQUIRED:
                raise StateConflict(f"compensation can be retried only from HUMAN_REQUIRED (is {rows.root.state})",
                                    code="COMPENSATION_NOT_PERMITTED")
            if any(e.state in (E.UNKNOWN,) or (e.state == E.HUMAN_REQUIRED and e.compensation_result is None)
                   for e in rows.effects.values()):
                raise StateConflict("unresolved UNKNOWN effects must be reconciled before compensation",
                                    code="BLOCKING_UNKNOWN")
            self._move_tree(s, rows, T.COMPENSATING, "compensation retry requested")
        return await self._run_compensation(root_id, retry_failed=True)

    async def _state(self, tx_id: UUID) -> str:
        async with self.db.read() as s:
            return (await get_tx(s, tx_id)).state

    # ======================================================= restart recovery
    async def resume_inflight(self) -> list[UUID]:
        """Resume every root left mid-flight by a crash or restart (from Postgres only)."""
        async with self.db.read() as s:
            roots = list((await s.execute(
                select(TransactionRow.id).where(
                    TransactionRow.parent_id.is_(None),
                    TransactionRow.state.in_([T.COMMITTING, T.VERIFYING, T.UNKNOWN, T.RECONCILING, T.COMPENSATING]))
                .order_by(TransactionRow.created_at))).scalars())
        for rid in roots:
            await self.recover_root(rid)
        return roots

    async def recover_root(self, root_id: UUID) -> str:
        with span("transaction.restart_recovery", transaction_id=root_id, root_id=root_id):
            async with self.db.uow() as s:
                rows = await load_tree(s, root_id, lock=True)
                in_flight = []
                for eff in rows.effects.values():
                    if E(eff.state) in (E.DISPATCHING, E.DISPATCHED, E.VERIFYING, E.RECONCILING):
                        attempts = (await s.execute(select(OperationAttemptRow).where(
                            OperationAttemptRow.effect_id == eff.id,
                            OperationAttemptRow.status == AttemptStatus.INTENT_RECORDED))).scalars()
                        for a in attempts:
                            a.status = str(AttemptStatus.ABANDONED_BY_RESTART)
                            a.finished_at = self.manager.clock()
                        rows.logical_ops[eff.logical_operation_id].status = str(LogicalOperationStatus.UNKNOWN)
                        self.manager.transition_effect(
                            s, rows.txs[eff.transaction_id], eff, E.UNKNOWN,
                            f"coordinator restarted while effect was {eff.state}; outcome unknown",
                            event_type=EventType.EFFECT_DISPATCH_UNKNOWN)
                        in_flight.append(eff.operation_key)
                append_event(s, rows.root, EventType.RESTART_RECOVERY, {
                    "state_at_restart": rows.root.state, "in_flight_effects": in_flight})
                state = T(rows.root.state)
                if in_flight and state in (T.COMMITTING, T.VERIFYING, T.RECONCILING):
                    self._move_tree(s, rows, T.UNKNOWN, "restart recovery found in-flight effects")
                    state = T.UNKNOWN
            if state == T.UNKNOWN:
                return await self.reconcile(root_id)
            if state == T.COMMITTING:
                return await self.drive(root_id)
            if state == T.VERIFYING:
                return await self._final_verification(root_id)
            if state == T.COMPENSATING:
                return await self._run_compensation(root_id)
            return state
