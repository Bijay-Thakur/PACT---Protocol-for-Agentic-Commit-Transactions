"""Transaction coordinator (Phase 2).

Lifecycle:  draft -> (children prepare locally) -> compile under the workflow policy ->
freeze revision (digest) -> approval bound to the digest -> binding barrier (freshness,
authority, invariants, operation identity, reservations, budgets) -> durable work item
-> worker steps (dispatch -> verify -> reconcile -> restore) -> truthful terminal
projection -> receipt.

All post-barrier progress runs in worker *steps* (``step``) claimed from the durable
work queue; every state write inside a step is fenced by the work item's epoch.
No database transaction is held open across provider or model calls.
"""

from __future__ import annotations

import asyncio
import fnmatch
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import provider_idempotency_key
from app.adapters.registry import UnsupportedContractVersion
from app.config import Settings
from app.core.commit_barrier import CommitBarrier
from app.core.compensator import Compensator
from app.core.effect_graph import EffectGraph
from app.core.evidence import execute_attempts
from app.core.executor import AuthorityLost, ExecutionContext, Executor
from app.core.invariant_engine import InvariantEngine
from app.core.receipt_generator import ReceiptGenerator, local_outcome
from app.core.reconciler import Reconciler
from app.core.reservations import BudgetCheck, BudgetService, ClaimRequest, ReservationService
from app.core.state_machine import TERMINAL_TX_STATES, TX_TRANSITIONS
from app.core.transaction_manager import SPECIFIABLE, TransactionManager
from app.core.verifier import Verifier
from app.core.work_queue import Claim, StaleWorker
from app.domain.decision import BarrierCheck, CommitDecision
from app.domain.enums import (
    Application,
    AttemptKind,
    AttemptStatus,
    BudgetEntryKind,
    CommitRequestStatus,
    EffectState,
    EventType,
    FailureAction,
    InvariantPhase,
    LogicalOperationStatus,
    ObservationPurpose,
    PlanRevisionStatus,
    Postcondition,
    ResidualDisposition,
    ResidualKind,
    Restoration,
    TransactionState,
    WorkKind,
)
from app.domain.errors import NotFound, StateConflict, ValidationFailed
from app.domain.invariant import InvariantEvaluation
from app.agents.semantic_judge import DeterministicSemanticJudge, JudgeService, judge_released
from app.domain.semantics import IntentSemantics, SemanticAssessment, assess_compiled_candidate
from app.domain.transaction import ApprovalRequest, CommitRequest, OperatorActionRequest, RecoveryPolicy
from app.persistence.models import (
    ApprovalRow,
    CommitDecisionRow,
    EffectDependencyRow,
    InvariantEvaluationRow,
    InvariantRow,
    IntentAcceptanceRow,
    LogicalOperationRow,
    OperationAttemptRow,
    PlanRevisionRow,
    PrincipalRow,
    ReceiptRow,
    ResidualObligationRow,
    SemanticAdjudicationRow,
    SemanticAssessmentRow,
    TransactionRow,
)
from app.persistence.repositories import (
    TreeRows, append_event, cap_node, effect_node, effect_view, get_tx, load_tree,
)
from app.policy.workflows import CompileInput, DraftCap, DraftEffect, Issue, WorkflowRegistry
from app.policy.digest import digest, fingerprint
from app.policy.outcomes import evaluate_required_outcome
from app.security.principals import Forbidden, Principal
from app.telemetry.tracing import span

T = TransactionState
E = EffectState

_PATHS: dict[tuple[T, T], list[T]] = {
    (T.CREATED, T.ABORTED): [T.ABORTING, T.ABORTED],
    (T.SPECIFYING, T.ABORTED): [T.ABORTING, T.ABORTED],
    (T.PREPARING, T.ABORTED): [T.ABORTING, T.ABORTED],
    (T.PREPARED, T.ABORTED): [T.ABORTING, T.ABORTED],
    (T.COMPENSATING, T.COMPENSATED): [T.COMPENSATED],
    (T.HUMAN_REQUIRED, T.COMPENSATED): [T.COMPENSATING, T.COMPENSATED],
}
POST_BARRIER = {T.COMMITTING, T.VERIFYING, T.UNKNOWN, T.RECONCILING, T.COMPENSATING, T.HUMAN_REQUIRED}
INFLIGHT = {E.DISPATCHING, E.DISPATCHED, E.VERIFYING, E.RECONCILING}


def _now() -> datetime:
    return datetime.now(UTC)


class Coordinator:
    def __init__(self, ctx: ExecutionContext, manager: TransactionManager, barrier: CommitBarrier,
                 invariants: InvariantEngine, receipts: ReceiptGenerator, workflows: WorkflowRegistry,
                 reservations: ReservationService, budgets: BudgetService, settings: Settings,
                 judge=None):
        self.ctx = ctx
        self.db = ctx.db
        self.manager = manager
        self.barrier = barrier
        self.invariants = invariants
        self.receipts = receipts
        self.workflows = workflows
        self.reservations = reservations
        self.budgets = budgets
        self.settings = settings
        self.judge = judge or DeterministicSemanticJudge()
        self.semantic_reviews = JudgeService(self.db, settings, lambda: self.judge)
        self.executor = Executor(ctx, self._authority_problems)
        self.verifier = Verifier(ctx)
        self.reconciler = Reconciler(ctx, self.verifier)
        self.compensator = Compensator(ctx, self.verifier)

    # =============================================================== helpers
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
                continue
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
                "observed": ev.observed_values})

    def _policy(self, rows: TreeRows) -> RecoveryPolicy:
        return RecoveryPolicy(**(rows.root.policy or {}))

    async def root_of(self, p: Principal, tx_id: UUID) -> UUID:
        async with self.db.read() as s:
            tx = await s.get(TransactionRow, tx_id)
            if tx is None or tx.tenant_id != p.tenant_id:
                raise NotFound(f"transaction {tx_id} not found", code="TRANSACTION_NOT_FOUND")
            await self.manager.assert_can_read(s, p, tx.root_id)
            return tx.root_id

    async def _frozen_revision(self, s: AsyncSession, root: TransactionRow) -> PlanRevisionRow | None:
        return (await s.execute(select(PlanRevisionRow).where(
            PlanRevisionRow.root_id == root.id, PlanRevisionRow.revision_no == root.current_revision))).scalar_one_or_none()

    async def _authority_problems(self, s: AsyncSession, rows: TreeRows) -> list[str]:
        """Execution authority re-checked immediately before every dispatch (forward progress only).

        Containment (reconciliation reads, restoring PACT's own changes) does not need approval and
        is never blocked by approval expiry; it never gains new authority either.
        """
        problems = []
        principal_ids = {t.principal_id for t in rows.txs.values() if t.principal_id}
        principals = {r.id: r for r in (await s.execute(select(PrincipalRow).where(
            PrincipalRow.id.in_(sorted(principal_ids, key=str))).with_for_update())).scalars()}
        for t in rows.txs.values():
            if t.principal_id and (t.principal_id not in principals or principals[t.principal_id].status != "ACTIVE"):
                problems.append(f"principal {t.actor_id} is revoked")
        initiator = principals.get(rows.root.principal_id)
        current_grant = None if initiator is None else (
            (initiator.grants.get("workflows") or {}).get(rows.root.workflow)
        )
        if initiator is not None and current_grant is None:
            problems.append("initiating workflow grant revoked")
        elif initiator is not None and current_grant is not None:
            workflow = self.workflows.get(rows.root.workflow)
            params = {
                key: value for key, value in (rows.root.meta or {}).items()
                if key in workflow.params_model.model_fields
            }
            try:
                current_cap = workflow.root_capability(params, current_grant, self.manager.clock())
            except ValidationFailed as exc:
                problems.append(f"initiating workflow grant no longer covers the target: {exc.code}")
            else:
                allowed_types = set(current_cap["allowed_effect_types"])
                allowed_resources = current_cap["allowed_resources"]
                amount_limit = Decimal(str(current_cap["amount_limit"]))
                cumulative_limit = Decimal(str(current_cap["cumulative_amount_limit"]))
                planned_total = Decimal("0")
                for effect in rows.effects.values():
                    if E(effect.state) == E.ABORTED:
                        continue
                    if effect.contract_type not in allowed_types:
                        problems.append(f"current grant no longer allows {effect.contract_type}")
                    for claim in effect.resource_claims or []:
                        resource = claim.get("resource", "")
                        if not any(fnmatch.fnmatchcase(resource, pattern) for pattern in allowed_resources):
                            problems.append(f"current grant no longer covers resource {resource}")
                    if effect.amount is not None:
                        planned_total += effect.amount
                        if effect.amount > amount_limit:
                            problems.append(
                                f"effect {effect.operation_key} exceeds current amount authority"
                            )
                if planned_total > cumulative_limit:
                    problems.append("planned cumulative amount exceeds current initiating grant")
        now = self.manager.clock()
        for effect in rows.effects.values():
            if E(effect.state) not in (E.PREPARED, E.RETRYABLE):
                continue
            tx = rows.txs.get(effect.transaction_id)
            cap = rows.caps.get(tx.capability_id) if tx is not None and tx.capability_id else None
            if cap is None:
                problems.append(f"{effect.operation_key} has no current capability")
                continue
            for violation in self.manager.authority.check_effect(cap_node(cap), effect_node(effect), now):
                problems.append(f"{effect.operation_key}: {violation.code}")
        for c in rows.caps.values():
            if c.expires_at is not None and c.expires_at <= now:
                problems.append(f"capability of {c.subject_id} expired")
        revision = await self._frozen_revision(s, rows.root)
        if revision is not None and revision.approval_required:
            ok, info = await self._approval_status(s, rows.root, revision, at_dispatch=True)
            if not ok:
                problems.append(f"approval no longer valid: {info.get('reason')}")
        return problems

    async def _approval_status(self, s: AsyncSession, root: TransactionRow, revision: PlanRevisionRow,
                               *, at_dispatch: bool = False) -> tuple[bool, dict[str, Any]]:
        if not revision.approval_required:
            return True, {"required": False}
        role = (revision.compiled or {}).get("approval", {}).get("role")
        rows = list((await s.execute(select(ApprovalRow).where(ApprovalRow.root_id == root.id)
                                     .order_by(ApprovalRow.created_at))).scalars())
        now = self.manager.clock()
        considered = []
        for a in rows:
            approver = (await s.execute(select(PrincipalRow).where(
                PrincipalRow.id == a.approver_principal_id).with_for_update())).scalar_one_or_none() if at_dispatch else \
                await s.get(PrincipalRow, a.approver_principal_id)
            why = None
            if a.digest != revision.digest or a.revision_id != revision.id:
                why = "bound to a different revision digest"
            elif a.revoked_at is not None:
                why = "revoked"
            elif a.expires_at <= now:
                why = "expired"
            elif approver is None or approver.status != "ACTIVE":
                why = "approver revoked"
            elif "op:approve" not in (approver.scopes or []) and "admin" not in (approver.scopes or []):
                why = "approval scope removed"
            elif role not in (approver.grants or {}).get("roles", []):
                why = "approver role removed"
            elif a.authorization_epoch != approver.authorization_epoch:
                why = "approver authority changed since approval"
            elif a.role != role:
                why = f"role {a.role} != required {role}"
            considered.append({"approver": a.approver_name, "digest": a.digest[:12], "valid": why is None,
                               "why_invalid": why})
            if why is None:
                return True, {"required": True, "role": role, "approver": a.approver_name, "considered": considered}
        return False, {"required": True, "role": role, "considered": considered,
                       "reason": "no valid approval bound to the frozen digest"}

    # =============================================================== prepare
    async def prepare(self, p: Principal, tx_id: UUID) -> dict[str, Any]:
        p.require("tx:prepare")
        async with self.db.read() as s:
            tx = await s.get(TransactionRow, tx_id)
            if tx is None or tx.tenant_id != p.tenant_id:
                raise NotFound(f"transaction {tx_id} not found", code="TRANSACTION_NOT_FOUND")
            if tx.principal_id != p.id:
                raise Forbidden("only the transaction's principal may prepare it", code="NOT_TRANSACTION_ACTOR")
            root_id = tx.root_id
            rows = await load_tree(s, root_id, lock=False)
            if rows.root.quarantined:
                raise StateConflict("legacy transaction is quarantined", code="QUARANTINED")
            sub = rows.snapshot().subtree_ids(tx_id)
            order = [t.id for t in sorted(rows.ordered_txs(), key=lambda t: -t.depth) if t.id in sub and t.id != root_id]
        for tid in order:
            await self._prepare_local(root_id, tid)
        if tx_id != root_id:
            async with self.db.read() as s:
                return {"transaction_id": str(tx_id), "state": (await get_tx(s, tx_id)).state, "scope": "local"}
        generation = await self._prepare_local(root_id, root_id)
        async with self.db.read() as s:
            root = await s.get(TransactionRow, root_id)
            if root.state == T.SPECIFYING and (root.meta or {}).get("prepare_failures"):
                return {"transaction_id": str(root_id), "status": "REJECTED", "state": root.state,
                        "issues": [{"code": "LOCAL_PREPARE_FAILED", "detail": reason}
                                   for reason in root.meta["prepare_failures"]],
                        "next_action": "REVISE_AUTHORITY_OR_DRAFT"}
        if generation is None:
            async with self.db.read() as s:
                root = await s.get(TransactionRow, root_id)
                if root.state != T.PREPARED:
                    return {"transaction_id": str(root_id), "status": "PREPARING",
                            "state": root.state, "next_action": "WAIT_FOR_ACTIVE_PREPARATION"}
        else:
            await self._ensure_semantic_judge(root_id, generation)
        return await self._compile(root_id, generation)

    async def _prepare_local(self, root_id: UUID, tx_id: UUID) -> int | None:
        """Local validity: own capability + read-only provider preconditions. Never mutates externally."""
        with span("transaction.prepare", transaction_id=tx_id, root_id=root_id):
            async with self.db.uow() as s:
                rows = await load_tree(s, root_id, lock=True)
                tx = rows.txs[tx_id]
                if T(tx.state) not in SPECIFIABLE:
                    return None
                tx.prepare_generation += 1
                generation = tx.prepare_generation
                revision = rows.root.current_revision
                if tx.state == T.CREATED:
                    self.manager.transition_tx(s, tx, T.SPECIFYING, "specification closed by prepare")
                self.manager.transition_tx(s, tx, T.PREPARING, "local authority and read-only preconditions")
                snap = rows.snapshot()
                cap = snap.cap_of(tx_id)
                now = self.manager.clock()
                local: dict[UUID, list[str]] = {}
                for e in snap.effects_in({tx_id}):
                    if e.state not in (E.VALIDATED,):
                        continue
                    violations = self.manager.authority.check_effect(cap, e, now)
                    local[e.id] = [f"{v.code}: {v.detail}" for v in violations]
                    append_event(s, tx, EventType.AUTHORITY_EVALUATED, {
                        "operation_key": e.operation_key, "passed": not violations,
                        "violations": [v.model_dump() for v in violations], "scope": "local (delegated capability)",
                    }, effect_id=e.id)
                views = {eid: effect_view(rows.effects[eid]) for eid in local}
                fingerprints = {eid: (fingerprint(rows.effects[eid].payload), dict(rows.effects[eid].payload))
                                for eid in views}
            prepared = {}
            for eid, view in views.items():
                if not local[eid]:
                    prepared[eid] = await self.ctx.registry.adapter(view.effect_type).prepare(view, self.ctx.adapter_ctx())
            async with self.db.uow() as s:
                rows = await load_tree(s, root_id, lock=True)
                tx = rows.txs[tx_id]
                if T(tx.state) != T.PREPARING or tx.prepare_generation != generation \
                        or rows.root.current_revision != revision or any(
                            eid not in rows.effects or fingerprint(rows.effects[eid].payload) != fp
                            or rows.effects[eid].payload != payload or E(rows.effects[eid].state) != E.VALIDATED
                            for eid, (fp, payload) in fingerprints.items()):
                    # A sweeper may have reset a stalled read-only prepare while
                    # the provider read was outstanding. Discard that stale read.
                    return None
                failures: list[str] = []
                for eid in sorted(views, key=lambda i: str(i)):
                    eff = rows.effects[eid]
                    reasons = list(local[eid])
                    result = prepared.get(eid)
                    if result is not None and not result.ok:
                        reasons.append(f"PRECONDITION_FAILED: {result.reason}")
                    if reasons:
                        failures.append(f"{eff.contract_type}: {'; '.join(reasons)}")
                        append_event(s, tx, EventType.EFFECT_PREPARE_FAILED,
                                     {"operation_key": eff.operation_key, "reasons": reasons}, effect_id=eid)
                        continue
                    eff.prepare_evidence = {
                        "observations": result.observations, "resolved": result.resolved,
                        "preconditions": result.preconditions,
                        "max_exposure": str(result.max_exposure) if result.max_exposure is not None else None,
                        "prepared_at": self.manager.clock().isoformat(), "provenance": self.ctx.provenance,
                        "prepare_generation": generation, "draft_revision": revision,
                        "payload_fingerprint": fingerprints[eid][0]}
                    self.manager.transition_effect(s, tx, eff, E.PREPARED, "local authority valid; preconditions hold",
                                                   event_type=EventType.EFFECT_PREPARED,
                                                   observations=result.observations, resolved=result.resolved)
                if failures:
                    # The draft stays open: the agent can withdraw / re-propose. Nothing is aborted.
                    tx.meta = {**(tx.meta or {}), "prepare_failures": failures}
                    self.manager.transition_tx(s, tx, T.SPECIFYING, "local prepare failed", failures=failures)
                elif tx_id != root_id:
                    tx.meta = {**(tx.meta or {}), "prepare_failures": []}
                    self.manager.transition_tx(s, tx, T.PREPARED, "locally prepared")
                else:
                    tx.meta = {**(tx.meta or {}), "prepare_failures": []}
                return generation

    async def _ensure_semantic_judge(self, root_id: UUID, generation: int) -> None:
        """Judge the candidate outside the compile lock, then publish by digest."""

        async with self.db.read() as session:
            root = await session.get(TransactionRow, root_id)
            if root is None or root.state != T.PREPARING or root.prepare_generation != generation:
                return
            acceptance = (await session.execute(select(IntentAcceptanceRow).where(
                IntentAcceptanceRow.root_id == root.id))).scalar_one_or_none()
            if acceptance is None or not acceptance.intent_semantics:
                return
            accepted = IntentSemantics.model_validate(acceptance.intent_semantics)
            if not accepted.source_text:
                return
            rows = await load_tree(session, root_id, lock=False)
            result = await self._build_compile_result(session, rows)
            if result.status != "COMPILED" or not result.digest:
                return
            snapshot = {
                "tenant_id": root.tenant_id,
                "principal_id": root.principal_id,
                "revision_no": root.current_revision,
                "candidate_digest": result.digest,
                "source_text": accepted.source_text,
                "accepted": accepted,
                "plan": _jsonable(result.plan),
            }
        await self.semantic_reviews.ensure(
            tenant_id=snapshot["tenant_id"], principal_id=snapshot["principal_id"], root_id=root_id,
            generation=generation, revision_no=snapshot["revision_no"],
            candidate_digest=snapshot["candidate_digest"], source_text=snapshot["source_text"],
            accepted=snapshot["accepted"], plan=snapshot["plan"],
        )

    async def _build_compile_result(self, s: AsyncSession, rows: TreeRows):
        root = rows.root
        wf = self.workflows.get(root.workflow)
        principal = await s.get(PrincipalRow, root.principal_id)
        if principal is None or principal.status != "ACTIVE":
            raise Forbidden("initiating principal is revoked", code="PRINCIPAL_REVOKED")
        grant = (principal.grants.get("workflows") or {}).get(wf.key) or {}
        live = [e for e in rows.effects.values() if E(e.state) in (E.VALIDATED, E.PREPARED)]
        unprepared = [t.actor_id for t in rows.txs.values()
                      if t.id != root.id and t.required and T(t.state) != T.PREPARED
                      and any(e.transaction_id == t.id for e in live)]
        unprepared_effects = [e for e in live if E(e.state) != E.PREPARED and e.transaction_id != root.id]
        inp = CompileInput(
            tenant_id=root.tenant_id, root_id=root.id, root_actor=root.actor_id,
            params={k: v for k, v in (root.meta or {}).items() if k in wf.params_model.model_fields},
            grant=grant,
            effects=[DraftEffect(id=e.id, transaction_id=e.transaction_id, actor=e.actor_id,
                                 effect_type=e.contract_type, slot=e.slot, payload=dict(e.payload),
                                 declared_depends_on=list(e.depends_on or []),
                                 client_operation_key=e.client_operation_key,
                                 prepare_evidence=dict(e.prepare_evidence or {}),
                                 prepared=E(e.state) == E.PREPARED,
                                 prior_identity=e.operation_key if e.revision_no is not None else None)
                     for e in live],
            caps=[DraftCap(transaction_id=c.transaction_id, subject=c.subject_id,
                           allowed_effect_types=list(c.scope["allowed_effect_types"]),
                           allowed_resources=list(c.scope["allowed_resources"]),
                           amount_limit=c.amount_limit, cumulative_limit=c.cumulative_limit,
                           delegation_depth=c.delegation_depth, expires_at=c.expires_at,
                           parent_capability_id=c.parent_capability_id) for c in rows.caps.values()],
            unprepared_children=sorted(set(unprepared) | {e.actor_id for e in unprepared_effects}),
            epoch=1, now=self.manager.clock())
        result = wf.compile(inp, self.ctx.registry)
        epoch = await self._epoch_for(s, root, result.business_request_key)
        if epoch != 1:
            inp.epoch = epoch
            result = wf.compile(inp, self.ctx.registry)
        if result.status == "COMPILED":
            await self._identity_issues(s, result)
        return result

    async def _record_assessment(self, s: AsyncSession, root: TransactionRow, assessment, document: dict,
                                 assessment_hash: str, candidate_digest: str) -> SemanticAssessmentRow:
        model = assessment.model or "unspecified"
        existing = (await s.execute(select(SemanticAssessmentRow).where(
            SemanticAssessmentRow.tenant_id == root.tenant_id,
            SemanticAssessmentRow.candidate_digest == candidate_digest,
            SemanticAssessmentRow.provider == assessment.provider,
            SemanticAssessmentRow.model == model,
            SemanticAssessmentRow.prompt_version == assessment.prompt_version,
            SemanticAssessmentRow.rubric_version == assessment.rubric_version,
            SemanticAssessmentRow.configuration_version == assessment.configuration_version,
        ))).scalar_one_or_none()
        if existing is not None:
            return existing
        row = SemanticAssessmentRow(
            tenant_id=root.tenant_id, root_id=root.id, revision_no=root.current_revision,
            prepare_generation=root.prepare_generation, candidate_digest=candidate_digest,
            assessment_hash=assessment_hash, aggregate=assessment.aggregate, provider=assessment.provider,
            model=model, prompt_version=assessment.prompt_version, schema_version=assessment.schema_version,
            rubric_version=assessment.rubric_version, configuration_version=assessment.configuration_version,
            assessment=document, usage=assessment.usage, latency_ms=assessment.latency_ms,
        )
        s.add(row)
        return row

    async def _judge_row(self, s: AsyncSession, tenant_id: str, candidate_digest: str) -> SemanticAssessmentRow | None:
        judge = self.judge
        return (await s.execute(select(SemanticAssessmentRow).where(
            SemanticAssessmentRow.tenant_id == tenant_id,
            SemanticAssessmentRow.candidate_digest == candidate_digest,
            SemanticAssessmentRow.provider == judge.name,
            SemanticAssessmentRow.model == judge.model,
            SemanticAssessmentRow.prompt_version == judge.prompt_version,
            SemanticAssessmentRow.rubric_version == judge.rubric_version,
            SemanticAssessmentRow.configuration_version == judge.configuration_version,
        ))).scalar_one_or_none()

    async def _compile(self, root_id: UUID, expected_generation: int | None = None) -> dict[str, Any]:
        with span("plan.compile", transaction_id=root_id, root_id=root_id):
            async with self.db.uow() as s:
                rows = await load_tree(s, root_id, lock=True)
                root = rows.root
                if expected_generation is not None and root.prepare_generation != expected_generation:
                    raise StateConflict("preparation was superseded", code="STALE_PREPARATION")
                if root.state == T.PREPARED:
                    rev = await self._frozen_revision(s, root)
                    return self._revision_summary(root, rev)
                if root.state != T.PREPARING:
                    raise StateConflict(f"root is {root.state}; cannot compile", code="SPECIFICATION_CLOSED")
                result = await self._build_compile_result(s, rows)
                wf = self.workflows.get(root.workflow)
                live = [e for e in rows.effects.values() if E(e.state) in (E.VALIDATED, E.PREPARED)]
                if result.status == "COMPILED" and result.digest:
                    acceptance = (await s.execute(select(IntentAcceptanceRow).where(
                        IntentAcceptanceRow.root_id == root.id))).scalar_one_or_none()
                    if acceptance is not None and acceptance.intent_semantics:
                        accepted = IntentSemantics.model_validate(acceptance.intent_semantics)
                        candidate_digest = result.digest
                        assessment = assess_compiled_candidate(accepted, result.plan, candidate_digest)
                        assessment_document = assessment.model_dump(mode="json")
                        assessment_hash = digest({"semantic_assessment": assessment_document})
                        await self._record_assessment(
                            s, root, assessment, assessment_document, assessment_hash, candidate_digest)
                        judge_row = await self._judge_row(s, root.tenant_id, candidate_digest)
                        adjudications = list((await s.execute(select(SemanticAdjudicationRow).where(
                            SemanticAdjudicationRow.tenant_id == root.tenant_id,
                            SemanticAdjudicationRow.root_id == root.id,
                            SemanticAdjudicationRow.candidate_digest == candidate_digest,
                        ))).scalars())
                        dismissed = {code for row in adjudications for code in (row.issue_codes or [])}
                        judge_document = None
                        judge_hash = None
                        released = True
                        if accepted.source_text:
                            if judge_row is None or judge_row.candidate_digest != candidate_digest:
                                released = False
                                result.issues.append(Issue(
                                    "SEMANTIC_JUDGE_REQUIRED",
                                    "natural-language candidate has no current advisory assessment",
                                    kind="CLARIFY"))
                            else:
                                judged = SemanticAssessment.model_validate(judge_row.assessment)
                                judge_document = judge_row.assessment
                                judge_hash = judge_row.assessment_hash
                                released = judge_released(judged, dismissed)
                                if not released:
                                    result.issues.extend(Issue(
                                        issue.code, issue.detail, kind="CLARIFY") for issue in judged.issues)
                        if assessment.aggregate != "PASS" or not released:
                            result.status = "NEEDS_CLARIFICATION"
                            if assessment.aggregate != "PASS":
                                result.issues.extend(Issue(
                                    issue.code, issue.detail, kind="CLARIFY") for issue in assessment.issues)
                        else:
                            bound_semantics = accepted.model_copy(update={"source_text": None}).model_dump(mode="json")
                            result.plan = {
                                **result.plan,
                                "candidate_digest": candidate_digest,
                                "intent_semantics": bound_semantics,
                                "semantic_assessment": assessment_document,
                                "semantic_assessment_hash": assessment_hash,
                                "judge_assessment": judge_document,
                                "judge_assessment_hash": judge_hash,
                                "adjudication_hashes": [
                                    digest({"candidate_digest": row.candidate_digest,
                                            "issue_codes": row.issue_codes, "evidence": row.evidence,
                                            "reason": row.reason})
                                    for row in adjudications
                                ],
                            }
                            result.digest = digest(result.plan)
                rev = await self._frozen_revision(s, root)
                if result.status != "COMPILED":
                    rev.status = str(PlanRevisionStatus.REJECTED if result.status == "REJECTED"
                                     else PlanRevisionStatus.NEEDS_CLARIFICATION)
                    rev.compile_result = result.summary()
                    root.current_revision += 1
                    s.add(PlanRevisionRow(tenant_id=root.tenant_id, root_id=root.id, revision_no=root.current_revision,
                                          status=str(PlanRevisionStatus.DRAFT), workflow=wf.key,
                                          workflow_version=wf.version, policy_version=wf.policy_version,
                                          created_by=root.principal_id))
                    self._reopen_draft(s, rows, f"compile {result.status.lower()}")
                    append_event(s, root, EventType.PLAN_REJECTED if result.status == "REJECTED"
                                 else EventType.PLAN_NEEDS_CLARIFICATION,
                                 {"revision": rev.revision_no, "issues": [i.as_dict() for i in result.issues]})
                    return {"transaction_id": str(root.id), "status": result.status, "revision": rev.revision_no,
                            "issues": [i.as_dict() for i in result.issues], "next_draft_revision": root.current_revision,
                            "state": root.state}
                # Freeze: the compiled execution payloads, identities and pins become the plan.
                compiled = {c.effect_id: c for c in result.effects}
                for eid, c in compiled.items():
                    eff = rows.effects[eid]
                    eff.slot, eff.payload, eff.operation_key = c.slot, c.payload, c.identity
                    eff.provider_idempotency_key = provider_idempotency_key(c.identity)
                    eff.payload_fingerprint, eff.amount, eff.currency = c.fingerprint, c.amount, c.currency
                    eff.max_exposure, eff.resource_claims = c.max_exposure, c.claims
                    eff.canonical_resource = c.claims[0]["resource"] if c.claims else None
                    eff.contract_id, eff.contract_version = c.contract["id"], c.contract["version"]
                    eff.contract_hash = c.contract["hash"]
                    eff.depends_on = sorted(compiled[d].identity for d in c.depends_on if d in compiled)
                    eff.revision_no = rev.revision_no
                for e in live:  # proposed but not part of the compiled plan
                    if e.id not in compiled:
                        self.manager.transition_effect(s, rows.txs[e.transaction_id], e, E.ABORTED,
                                                       "not part of the compiled plan", event_type=EventType.EFFECT_RELEASED)
                existing = {i.key for i in rows.invariants if i.transaction_id == root.id}
                for inv in result.invariants:
                    if inv.key not in existing:
                        row = InvariantRow(transaction_id=root.id, key=inv.key, name=inv.name, phase=str(inv.phase),
                                           expression_type=inv.expression_type, definition=inv.config,
                                           severity=str(inv.severity), failure_action=str(inv.failure_action),
                                           description=f"policy pack {wf.policy_version}")
                        s.add(row)
                        rows.invariants.append(row)
                        append_event(s, root, EventType.INVARIANT_REGISTERED,
                                     {**inv.model_dump(mode="json"), "source": wf.policy_version})
                root.business_request_key = result.business_request_key
                root.meta = {**(root.meta or {}), "epoch": (result.plan or {}).get("epoch", 1)}
                rev.status, rev.compiled, rev.digest = str(PlanRevisionStatus.FROZEN), _jsonable(result.plan), result.digest
                if result.plan.get("candidate_digest"):
                    rev.candidate_digest = result.plan["candidate_digest"]
                    rev.semantic_assessment_hash = result.plan["semantic_assessment_hash"]
                    rev.semantic_disposition = result.plan["semantic_assessment"]["aggregate"]
                rev.compile_result, rev.approval_required = result.summary(), bool(result.approval.get("required"))
                rev.frozen_at = self.manager.clock()
                self.manager.transition_tx(s, root, T.PREPARED, "plan compiled and frozen", digest=result.digest,
                                           revision=rev.revision_no)
                append_event(s, root, EventType.PLAN_FROZEN, {
                    "revision": rev.revision_no, "digest": result.digest, "approval": result.approval,
                    "business_request_key": result.business_request_key, "skipped": result.skipped,
                    "budgets": [{**b, "limit": str(b["limit"])} for b in result.budgets]})
                return self._revision_summary(root, rev)

    async def _epoch_for(self, s: AsyncSession, root: TransactionRow, brk: str) -> int:
        if not (root.meta or {}).get("epoch_request", {}).get("new"):
            return int((root.meta or {}).get("epoch", 1))
        current = (await s.execute(text("SELECT coalesce(max(epoch), 0) FROM logical_operations "
                                        "WHERE tenant_id = :t AND business_request_key = :b"),
                                   {"t": root.tenant_id, "b": brk})).scalar_one()
        return int(current) + 1

    async def _identity_issues(self, s: AsyncSession, result) -> None:
        """Same identity + different fingerprint conflicts; satisfied identities are not re-executed (A13)."""
        keys = [c.identity for c in result.effects]
        existing = {o.operation_key: o for o in (await s.execute(select(LogicalOperationRow).where(
            LogicalOperationRow.operation_key.in_(keys)))).scalars()}
        for c in result.effects:
            lop = existing.get(c.identity)
            if lop is None:
                continue
            if lop.status == LogicalOperationStatus.VERIFIED and lop.payload_fingerprint == c.fingerprint:
                result.issues.append(Issue("OPERATION_ALREADY_SATISFIED",
                                           f"{c.slot}: this business operation was already executed and verified "
                                           "(same identity, same payload); it will not run again", slot=c.slot))
            elif lop.payload_fingerprint and lop.payload_fingerprint != c.fingerprint and \
                    lop.status != LogicalOperationStatus.AVAILABLE:
                result.issues.append(Issue("OPERATION_FINGERPRINT_CONFLICT",
                                           f"{c.slot}: identity {c.identity} is bound to a different payload "
                                           f"(status {lop.status}); a new business epoch is required", slot=c.slot))
            elif lop.status in (LogicalOperationStatus.FAILED, LogicalOperationStatus.COMPENSATED):
                result.issues.append(Issue("OPERATION_CLOSED_NEW_EPOCH_REQUIRED",
                                           f"{c.slot}: identity was {lop.status}; re-running it needs a new epoch",
                                           slot=c.slot))
        if any(i.kind == "REJECT" for i in result.issues):
            result.status, result.digest = "REJECTED", None

    def _reopen_draft(self, s: AsyncSession, rows: TreeRows, reason: str) -> None:
        for t in rows.ordered_txs():
            t.prepare_generation += 1
            if T(t.state) in (T.PREPARED, T.PREPARING):
                self.manager.transition_tx(s, t, T.SPECIFYING, reason)
        for e in rows.effects.values():
            if E(e.state) == E.PREPARED:
                self.manager.transition_effect(s, rows.txs[e.transaction_id], e, E.VALIDATED,
                                               "draft reopened; must be prepared again")

    def _revision_summary(self, root: TransactionRow, rev: PlanRevisionRow | None) -> dict[str, Any]:
        return {"transaction_id": str(root.id), "status": "FROZEN" if rev and rev.digest else "DRAFT",
                "state": root.state, "revision": rev.revision_no if rev else None,
                "digest": rev.digest if rev else None,
                "approval": (rev.compiled or {}).get("approval") if rev else None,
                "issues": (rev.compile_result or {}).get("issues", []) if rev else [],
                "skipped": (rev.compile_result or {}).get("skipped", []) if rev else [],
                "budgets": (rev.compile_result or {}).get("budgets", []) if rev else [],
                "next_action": ("APPROVE" if rev and rev.approval_required else "REQUEST_COMMIT") if rev and rev.digest
                else "PROPOSE"}

    async def revise(self, p: Principal, root_id: UUID, reason: str) -> dict[str, Any]:
        p.require("tx:prepare")
        async with self.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            root = rows.root
            if root.tenant_id != p.tenant_id or root.principal_id != p.id:
                raise Forbidden("only the initiating principal may revise the plan", code="NOT_TRANSACTION_ACTOR")
            if root.state != T.PREPARED:
                raise StateConflict(f"only a frozen, not-yet-committed plan can be revised (is {root.state}); "
                                    "executed plans need a separate remediation transaction", code="REVISION_NOT_ALLOWED")
            rev = await self._frozen_revision(s, root)
            rev.status = str(PlanRevisionStatus.SUPERSEDED)
            root.current_revision += 1
            current_workflow = self.workflows.get(root.workflow)
            root.workflow_version = current_workflow.version
            s.add(PlanRevisionRow(tenant_id=root.tenant_id, root_id=root.id, revision_no=root.current_revision,
                                  status=str(PlanRevisionStatus.DRAFT), workflow=root.workflow,
                                  workflow_version=current_workflow.version,
                                  policy_version=current_workflow.policy_version,
                                  created_by=p.id))
            self._reopen_draft(s, rows, f"revision requested: {reason}")
            append_event(s, root, EventType.PLAN_REVISED, {"superseded": rev.revision_no, "digest": rev.digest,
                                                           "new_draft": root.current_revision, "reason": reason,
                                                           "note": "approvals bound to the old digest no longer apply"},
                         actor=p.name)
            return {"transaction_id": str(root.id), "state": root.state, "draft_revision": root.current_revision}

    # ============================================================== approvals
    async def approve(self, p: Principal, root_id: UUID, req: ApprovalRequest) -> dict[str, Any]:
        from app.security.principals import reject_forged

        p.require("op:approve")
        reject_forged(p, operator_id=req.operator_id)
        async with self.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            root = rows.root
            if root.tenant_id != p.tenant_id:
                raise NotFound(f"transaction {root_id} not found", code="TRANSACTION_NOT_FOUND")
            if str(p.kind) != "OPERATOR":
                raise Forbidden("approval is an authenticated operator workflow", code="OPERATOR_REQUIRED")
            if p.id in {t.principal_id for t in rows.txs.values()}:
                raise Forbidden("a participant cannot approve its own protected action", code="SELF_APPROVAL")
            rev = await self._frozen_revision(s, root)
            if root.state != T.PREPARED or rev is None or rev.digest is None:
                raise StateConflict("nothing frozen to approve", code="NOTHING_TO_APPROVE")
            if rev.digest != req.revision_digest:
                raise StateConflict("approval digest does not match the current frozen revision",
                                    code="REVISION_DIGEST_MISMATCH", details={"current": rev.digest})
            role = (rev.compiled or {}).get("approval", {}).get("role") or "approver"
            current = (await s.execute(select(PrincipalRow).where(
                PrincipalRow.id == p.id).with_for_update())).scalar_one_or_none()
            if current is None or current.status != "ACTIVE" or role not in (current.grants or {}).get("roles", []) \
                    or "op:approve" not in (current.scopes or []):
                raise Forbidden(f"approver lacks role {role!r}", code="APPROVER_ROLE_MISSING")
            ttl = timedelta(minutes=float(self.settings.approval_ttl_minutes))
            s.add(ApprovalRow(tenant_id=root.tenant_id, root_id=root.id, revision_id=rev.id, digest=rev.digest,
                              approver_principal_id=p.id, approver_name=p.name, role=role, reason=req.reason,
                              expires_at=self.manager.clock() + ttl,
                              authorization_epoch=current.authorization_epoch))
            await self.manager.record_operator_action(s, root, p, "APPROVE", req.reason,
                                                      payload={"digest": rev.digest, "role": role})
            append_event(s, root, EventType.APPROVAL_RECORDED, {"digest": rev.digest, "approver": p.name, "role": role,
                                                                "reason": req.reason}, actor=f"operator:{p.name}")
            return {"transaction_id": str(root.id), "approved_digest": rev.digest, "role": role,
                    "expires_in_minutes": self.settings.approval_ttl_minutes}

    # ======================================================== binding barrier
    async def evaluate_barrier(self, root_id: UUID) -> CommitDecision:
        """Dry run (read-only): no freshness reads, no reservations taken."""
        async with self.db.read() as s:
            rows = await load_tree(s, root_id, lock=False)
            extra = []
            rev = await self._frozen_revision(s, rows.root)
            if rev is not None and rev.digest:
                ok, info = await self._approval_status(s, rows.root, rev)
                extra.append(BarrierCheck(code="APPROVAL_BOUND_TO_DIGEST", passed=ok, detail=info.get("reason", "ok"),
                                          observed=info, blocking_reason=None if ok else "APPROVAL_REQUIRED"))
            extra.append(BarrierCheck(code="RESERVATIONS", passed=True, detail="not evaluated in a dry run"))
            return self.barrier.evaluate(rows.snapshot(), self.manager.clock(), binding=False,
                                         extra_checks=extra).decision

    async def request_commit(self, p: Principal, root_id: UUID, req: CommitRequest) -> dict[str, Any]:
        p.require("tx:commit")
        async with self.db.read() as s:
            root = await s.get(TransactionRow, root_id)
            if root is None or root.tenant_id != p.tenant_id:
                raise NotFound(f"transaction {root_id} not found", code="TRANSACTION_NOT_FOUND")
            if root.parent_id is not None:
                raise StateConflict("child transactions cannot commit independently; only the root may cross "
                                    "the commit barrier", code="CHILD_CANNOT_COMMIT_INDEPENDENTLY")
            if root.principal_id != p.id:
                raise Forbidden("only the initiating principal may request commit", code="NOT_TRANSACTION_ACTOR")
            rev = await self._frozen_revision(s, root)
            if T(root.state) in POST_BARRIER or T(root.state) in TERMINAL_TX_STATES:
                if rev is not None and rev.digest == req.revision_digest:
                    return {"transaction_id": str(root_id),
                            "status": "ALREADY_TERMINAL" if T(root.state) in TERMINAL_TX_STATES else "QUEUED",
                            "replayed": True, "state": root.state,
                            "note": "already crossed the barrier; nothing re-enqueued"}
                raise StateConflict(f"cannot commit a transaction in state {root.state}",
                                    code="COMMIT_ALREADY_IN_PROGRESS" if T(root.state) in POST_BARRIER
                                    else "COMMIT_NOT_PERMITTED")
            if root.state != T.PREPARED or rev is None or not rev.digest:
                raise StateConflict(f"transaction is {root.state}; prepare and freeze a plan first",
                                    code="COMMIT_NOT_PERMITTED")
            if rev.digest != req.revision_digest:
                raise StateConflict("requested digest is not the current frozen revision",
                                    code="REVISION_DIGEST_MISMATCH", details={"current": rev.digest})
            current_workflow = self.workflows.get(root.workflow)
            if rev.policy_version != current_workflow.policy_version:
                raise StateConflict("workflow policy changed; revise and prepare before dispatch",
                                    code="POLICY_REVISION_REQUIRED")
            rows = await load_tree(s, root_id, lock=False)
            views = [effect_view(e) for e in rows.effects.values() if E(e.state) == E.PREPARED]
        stale: dict[str, Any] = {}
        for v in views:  # provider freshness (read-only, outside any DB transaction)
            fresh, details = await self.ctx.registry.adapter(v.effect_type).freshness(v, self.ctx.adapter_ctx())
            if not fresh:
                stale[v.operation_key] = details
        with span("commit_barrier.evaluate", transaction_id=root_id, root_id=root_id) as sp:
            async with self.db.uow() as s:
                rows = await load_tree(s, root_id, lock=True)
                root = rows.root
                rev = await self._frozen_revision(s, root)
                if root.state != T.PREPARED or rev is None or rev.digest != req.revision_digest:
                    raise StateConflict("plan changed while committing", code="COMMIT_ALREADY_IN_PROGRESS")
                extra = [BarrierCheck(code="PLAN_FROZEN", passed=True, subject=f"revision {rev.revision_no}",
                                      detail=f"digest {rev.digest[:16]}...", observed={"digest": rev.digest})]
                extra.append(BarrierCheck(code="NOT_QUARANTINED", passed=not root.quarantined,
                                          detail=root.quarantine_reason or "ok",
                                          blocking_reason=None if not root.quarantined else "QUARANTINED"))
                extra.append(BarrierCheck(code="PROVIDER_FRESHNESS", passed=not stale,
                                          detail="all frozen preconditions still hold" if not stale
                                          else "provider state changed since the plan was frozen; revise",
                                          observed={"stale": stale}, blocking_reason=None if not stale else "STALE_PLAN"))
                ok, info = await self._approval_status(s, root, rev, at_dispatch=True)
                extra.append(BarrierCheck(code="APPROVAL_BOUND_TO_DIGEST", passed=ok,
                                          detail=info.get("reason", "approved" if info.get("required") else "not required"),
                                          observed=info, blocking_reason=None if ok else "APPROVAL_REQUIRED"))
                authority = await self._authority_problems(s, rows) if ok else []
                extra.append(BarrierCheck(code="EXECUTION_AUTHORITY", passed=not authority,
                                          detail="; ".join(authority) or "principals active; capabilities unexpired",
                                          blocking_reason=None if not authority else "AUTHORITY_LOST"))
                lops = await self._bind_operations(s, rows)
                result = self.barrier.evaluate(rows.snapshot(), self.manager.clock(), binding=True, extra_checks=extra)
                d = result.decision
                budget_checks: list[BudgetCheck] = []
                conflicts = []
                if d.eligible:
                    budget_checks, holds = await self._budget_checks(s, rows, rev)
                    for bc in budget_checks:
                        d.checks.append(BarrierCheck(code="BUDGET_CAPACITY", passed=bc.passed, subject=bc.key,
                                                     detail=f"requested {bc.requested} of available {bc.available}",
                                                     observed=bc.as_dict(),
                                                     blocking_reason=None if bc.passed else f"BUDGET_EXCEEDED:{bc.key}"))
                    if all(bc.passed for bc in budget_checks):
                        claims = [ClaimRequest(effect_id=e.id, resource=c["resource"], mode=c["mode"])
                                  for e in rows.effects.values() if E(e.state) == E.PREPARED for c in e.resource_claims]
                        conflicts = await self.reservations.reserve(s, root.tenant_id, root.id, claims)
                        d.checks.append(BarrierCheck(code="RESOURCE_RESERVATIONS", passed=not conflicts,
                                                     detail="reserved" if not conflicts else
                                                     f"{len(conflicts)} resource(s) held by other transactions",
                                                     observed={"conflicts": [c.as_dict() for c in conflicts],
                                                               "claims": len(claims)},
                                                     blocking_reason=None if not conflicts else "RESOURCE_RESERVED_BY_OTHER"))
                    d.blocking_reasons = [c.blocking_reason for c in d.checks if not c.passed and c.blocking_reason]
                    d.blocking_reasons = list(dict.fromkeys(d.blocking_reasons))
                    d.eligible = not d.blocking_reasons
                sp.set_attribute("pact.eligible", d.eligible)
                s.add(CommitDecisionRow(transaction_id=root_id, eligible=d.eligible, snapshot_version=d.snapshot_version,
                                        checks=[c.model_dump(mode="json") for c in d.checks],
                                        blocking_reasons=d.blocking_reasons, evaluated_at=d.evaluated_at))
                self._persist_evaluations(s, rows, result.invariant_evaluations, "commit_barrier")
                append_event(s, root, EventType.COMMIT_BARRIER_EVALUATED, {
                    "eligible": d.eligible, "blocking_reasons": d.blocking_reasons, "digest": rev.digest,
                    "checks": [{"code": c.code, "subject": c.subject, "passed": c.passed} for c in d.checks]},
                    actor=p.name)
                if not d.eligible:
                    status = (CommitRequestStatus.AWAITING_APPROVAL if d.blocking_reasons == ["APPROVAL_REQUIRED"]
                              else CommitRequestStatus.BLOCKED)
                    return {"transaction_id": str(root_id), "status": str(status), "state": root.state,
                            "decision": d.model_dump(mode="json"), "digest": rev.digest}
                for bc, (scope, amounts) in zip(budget_checks, holds):
                    for eid, amt in amounts:
                        self.budgets.entry(s, scope, root.id, eid, BudgetEntryKind.HOLD, amt, "barrier hold (requested)")
                append_event(s, root, EventType.BUDGET_HELD, {"budgets": [bc.as_dict() for bc in budget_checks]})
                for eff in rows.effects.values():
                    if E(eff.state) == E.PREPARED:
                        lop = lops[eff.id]
                        lop.status, lop.owner_root_id, lop.owner_effect_id = (
                            str(LogicalOperationStatus.RESERVED), root.id, eff.id)
                graph = EffectGraph([n for n in rows.snapshot().effects if n.state == E.PREPARED])
                s.add_all([EffectDependencyRow(from_effect_id=a, to_effect_id=b) for a, b in graph.edges()])
                append_event(s, root, EventType.RESERVATIONS_TAKEN, {
                    "resources": sorted({c["resource"] for e in rows.effects.values() if E(e.state) == E.PREPARED
                                         for c in e.resource_claims}),
                    "operations": sorted(lop.operation_key for lop in lops.values())})
                rev.status = str(PlanRevisionStatus.EXECUTING)
                root.meta = {**(root.meta or {}), "step_delay_s": min(req.step_delay_ms, 3000) / 1000
                             if self.settings.demo_mode else 0, "reconcile_rounds": 0}
                self._move_tree(s, rows, T.COMMITTING, "global commit barrier passed; execution queued")
                await self.ctx.queue.enqueue(s, root.tenant_id, root.id, WorkKind.DRIVE, reason="commit")
                append_event(s, root, EventType.COMMIT_REQUESTED, {"digest": rev.digest, "by": p.name,
                                                                   "status": "QUEUED"}, actor=p.name)
                return {"transaction_id": str(root_id), "status": str(CommitRequestStatus.QUEUED), "state": T.COMMITTING,
                        "decision": d.model_dump(mode="json"), "digest": rev.digest}

    async def _bind_operations(self, s: AsyncSession, rows: TreeRows) -> dict[UUID, LogicalOperationRow]:
        """Upsert and lock logical operations (stable key order); bind effects; fingerprint is immutable."""
        live = sorted((e for e in rows.effects.values() if E(e.state) == E.PREPARED), key=lambda e: e.operation_key)
        if not live:
            return {}
        for e in live:
            parts = e.operation_key.split("/")
            await s.execute(pg_insert(LogicalOperationRow).values(
                tenant_id=e.tenant_id, operation_key=e.operation_key, effect_type=e.contract_type,
                status=str(LogicalOperationStatus.AVAILABLE), provider=parts[1] if len(parts) > 1 else None,
                business_request_key=rows.root.business_request_key, slot=e.slot,
                canonical_resource=e.canonical_resource, epoch=int((rows.root.meta or {}).get("epoch", 1)),
                payload_fingerprint=e.payload_fingerprint, provider_idempotency_key=e.provider_idempotency_key,
                version=1).on_conflict_do_nothing(index_elements=["operation_key"]))
        found = {o.operation_key: o for o in (await s.execute(select(LogicalOperationRow).where(
            LogicalOperationRow.operation_key.in_([e.operation_key for e in live]))
            .order_by(LogicalOperationRow.operation_key).with_for_update())).scalars()}
        out = {}
        for e in live:
            lop = found[e.operation_key]
            if lop.status == LogicalOperationStatus.AVAILABLE and lop.payload_fingerprint != e.payload_fingerprint:
                lop.payload_fingerprint = e.payload_fingerprint  # never dispatched: safe to re-bind
            e.logical_operation_id = lop.id
            out[e.id] = lop
            rows.logical_ops[lop.id] = lop
        return out

    async def _budget_checks(self, s: AsyncSession, rows: TreeRows, rev: PlanRevisionRow):
        budgets = (rev.compiled or {}).get("budgets", [])
        checks, holds = [], []
        slot_effects: dict[str, list] = {}
        for e in rows.effects.values():
            if E(e.state) == E.PREPARED and e.amount is not None:
                slot_effects.setdefault(e.slot, []).append(e)
        for b in sorted(budgets, key=lambda b: b["key"]):
            scope = await self.budgets.scope(s, rows.root.tenant_id, b["key"], Decimal(str(b["limit"])),
                                             b["currency"], b.get("description", ""))
            consumed, held = await self.budgets.balance(s, scope.id)
            amounts = [(e.id, Decimal(str(e.amount))) for e in slot_effects.get(b.get("slot"), [])]
            checks.append(BudgetCheck(key=b["key"], limit=scope.limit_amount, consumed=consumed, held=held,
                                      requested=sum((a for _, a in amounts), Decimal("0")), currency=scope.currency))
            holds.append((scope, amounts))
        return checks, holds

    # ================================================================== abort
    async def abort(self, p: Principal, root_id: UUID, reason: str) -> dict[str, Any]:
        async with self.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            root = rows.root
            if root.tenant_id != p.tenant_id:
                raise NotFound(f"transaction {root_id} not found", code="TRANSACTION_NOT_FOUND")
            if not (root.principal_id == p.id and p.has("tx:abort")) and not p.has("op:recover"):
                raise Forbidden("only the initiating principal or an operator may abort", code="NOT_TRANSACTION_ACTOR")
            st = T(root.state)
            if st in TERMINAL_TX_STATES:
                raise StateConflict(f"transaction already {st}", code="TRANSACTION_FINALIZED")
            if st in (T.CREATED, T.SPECIFYING, T.PREPARING, T.PREPARED):
                self._move_tree(s, rows, T.ABORTED, f"aborted by {p.name}: {reason}")
                for e in rows.effects.values():
                    if E(e.state) in (E.PROPOSED, E.VALIDATED, E.PREPARED):
                        self.manager.transition_effect(s, rows.txs[e.transaction_id], e, E.ABORTED,
                                                       "transaction aborted before dispatch",
                                                       event_type=EventType.EFFECT_RELEASED)
                append_event(s, root, EventType.TRANSACTION_ABORT_REQUESTED, {"by": p.name, "reason": reason,
                                                                              "dispatched": False}, actor=p.name)
                await self._finalize_rows(s, rows)
                return {"transaction_id": str(root_id), "status": "ABORTED", "state": root.state}
            # After dispatch: never claim "nothing happened". Stop new dispatches and restore what applied.
            root.meta = {**(root.meta or {}), "abort_requested": {"by": p.name, "reason": reason}}
            append_event(s, root, EventType.TRANSACTION_ABORT_REQUESTED, {
                "by": p.name, "reason": reason, "dispatched": True,
                "note": "effects may already be applied; routing through recovery (restore applied effects)"},
                actor=p.name)
            await self.ctx.queue.enqueue(s, root.tenant_id, root.id, WorkKind.DRIVE, reason="abort requested")
            return {"transaction_id": str(root_id), "status": "ABORT_REQUESTED_RECOVERY", "state": root.state}

    # ================================================================ worker
    async def step(self, claim: Claim) -> float | None:
        """One unit of durable progress for a root. Returns the delay until the next step, or None."""
        root_id = claim.root_id
        async with self.db.uow() as s:
            await self.ctx.queue.fence(s, claim)
            rows = await load_tree(s, root_id, lock=True)
            root = rows.root
            if root.quarantined:
                return None
            await self._take_over(s, rows, claim)
            st = T(root.state)
            delay = float((root.meta or {}).get("step_delay_s") or 0)
            if st in TERMINAL_TX_STATES:
                await self._finalize_rows(s, rows)
                return None
            if st == T.ABORTING:
                self._move_tree(s, rows, T.ABORTED, "abort completed")
                await self._finalize_rows(s, rows)
                return None
            if st in (T.HUMAN_REQUIRED, T.PREPARED, T.SPECIFYING, T.CREATED, T.PREPARING):
                return None
            if st == T.UNKNOWN:
                policy = self._policy(rows)
                rounds = int((root.meta or {}).get("reconcile_rounds", 0))
                if policy.on_unknown == "RECONCILE" and policy.auto_reconcile and rounds < policy.max_auto_reconcile_rounds:
                    root.meta = {**(root.meta or {}), "reconcile_rounds": rounds + 1}
                    self._move_tree(s, rows, T.RECONCILING, f"automatic reconciliation round {rounds + 1}")
                    return 0.0
                if policy.on_unknown == "HUMAN_REQUIRED":
                    self._move_tree(s, rows, T.HUMAN_REQUIRED,
                                    "recovery policy requires a human for the unresolved outcome")
                    return None
                append_event(s, root, EventType.EXECUTION_SCHEDULE_EVALUATED, {
                    "waiting": "operator reconciliation", "reconcile_rounds": rounds})
                return None
            if st == T.COMMITTING:
                action = self._next_execution(rows)
                if (root.meta or {}).get("abort_requested") and action[0] in ("dispatch", "final"):
                    action = ("recover",)
                if action[0] == "recover":
                    await self._recovery_decision(s, rows)
                    return 0.0
                if action[0] == "unknown":
                    self._move_tree(s, rows, T.UNKNOWN, "an effect outcome is UNKNOWN; execution halted (no blind retry)",
                                    unknown=action[1])
                    return 0.0
                if action[0] == "stuck":
                    self._move_tree(s, rows, T.HUMAN_REQUIRED, "no effect is ready and none is in flight",
                                    waiting=action[1])
                    return None
                if action[0] == "dispatch":
                    append_event(s, root, EventType.EXECUTION_SCHEDULE_EVALUATED, action[2])
            elif st == T.RECONCILING:
                targets = sorted((e for e in rows.effects.values() if E(e.state) in (E.UNKNOWN, E.RECONCILING)),
                                 key=lambda e: e.operation_key)
                if not targets:
                    if any(E(e.state) == E.HUMAN_REQUIRED and e.compensation_result is None for e in rows.effects.values()):
                        self._move_tree(s, rows, T.HUMAN_REQUIRED, "reconciliation requires a human decision")
                        return None
                    self._move_tree(s, rows, T.COMMITTING, "ambiguity resolved; resuming ordered execution")
                    return 0.0
                action = ("reconcile", targets[0].id)
            elif st == T.VERIFYING:
                action = ("final",)
            elif st == T.COMPENSATING:
                nxt = self.compensator.next_candidate(rows)
                if nxt is None:
                    await self._finish_restoration(s, rows)
                    return None
                action = ("restore", nxt)
            else:
                return None
        # ---- external I/O happens outside the database transaction ----
        try:
            if action[0] == "dispatch":
                outcome = await self.executor.dispatch(root_id, action[1], claim)
                if outcome is not None and outcome.value == "ACCEPTED":
                    if delay:
                        await asyncio.sleep(delay)
                    await self.verifier.verify_effect(root_id, action[1], claim)
                return delay
            if action[0] == "reconcile":
                res = await self.reconciler.reconcile_effect(root_id, action[1], claim)
                if res.outcome == "STILL_UNKNOWN":
                    async with self.db.uow() as s:
                        await self.ctx.queue.fence(s, claim)
                        rows = await load_tree(s, root_id, lock=True)
                        self._move_tree(s, rows, T.UNKNOWN, "outcome still unknown after reconciliation")
                    return res.retry_after_s or 1.0
                return delay
            if action[0] == "final":
                return await self._final_verification(root_id, claim)
            if action[0] == "restore":
                await self.compensator.restore_effect(root_id, action[1], claim)
                return delay
        except (AuthorityLost, UnsupportedContractVersion) as exc:
            async with self.db.uow() as s:
                await self.ctx.queue.fence(s, claim)
                rows = await load_tree(s, root_id, lock=True)
                await self._release_unsent(s, rows, f"held: {exc.code}")
                self._move_tree(s, rows, T.HUMAN_REQUIRED, exc.message, code=exc.code)
            return None
        return None

    def _next_execution(self, rows: TreeRows) -> tuple:
        snap = rows.snapshot()
        live = [e for e in snap.effects if e.state != E.ABORTED]
        failed = [e for e in live if e.state == E.FAILED]
        unresolved = [e for e in live if e.state in (E.UNKNOWN, E.RECONCILING, E.HUMAN_REQUIRED)]
        if failed:
            return ("recover",)
        if unresolved:
            return ("unknown", [e.operation_key for e in unresolved])
        graph = EffectGraph(live)
        pending = [e for e in graph.topological_order() if e.state in (E.PREPARED, E.RETRYABLE)]
        if not pending:
            return ("final",)
        ready = [e for e in pending if graph.is_ready(e.id)]
        waiting = {e.operation_key: [d.operation_key + f" ({d.state})" for d in graph.unverified_dependencies(e.id)]
                   for e in pending if not graph.is_ready(e.id)}
        if not ready:
            return ("stuck", waiting)
        return ("dispatch", ready[0].id, {"next": ready[0].operation_key, "ready": [e.operation_key for e in ready],
                                          "waiting_on_verification": waiting})

    async def _take_over(self, s: AsyncSession, rows: TreeRows, claim: Claim) -> None:
        """Effects left mid-flight by an earlier (expired) owner become UNKNOWN; reconcile before replay."""
        taken = []
        for eff in rows.effects.values():
            st = E(eff.state)
            attempts = list((await s.execute(select(OperationAttemptRow).where(
                OperationAttemptRow.effect_id == eff.id,
                OperationAttemptRow.status == AttemptStatus.INTENT_RECORDED))).scalars())
            stale = [a for a in attempts if a.worker_epoch is None or a.worker_epoch < claim.epoch]
            if st in INFLIGHT or (st == E.COMPENSATING and stale):
                for a in stale:
                    a.status, a.finished_at = str(AttemptStatus.ABANDONED_BY_RESTART), self.manager.clock()
                if st in (E.DISPATCHING, E.DISPATCHED, E.VERIFYING, E.RECONCILING):
                    from app.domain.verification import Observation

                    obs = Observation(application=Application.UNKNOWN, postcondition=eff.postcondition,
                                      reason="previous worker stopped while the operation was in flight")
                    await self.ctx.evidence.apply_outcome(s, rows.txs[eff.transaction_id], eff, obs, None)
                    if eff.logical_operation_id and eff.logical_operation_id in rows.logical_ops:
                        rows.logical_ops[eff.logical_operation_id].status = str(LogicalOperationStatus.UNKNOWN)
                    self.manager.transition_effect(s, rows.txs[eff.transaction_id], eff, E.UNKNOWN,
                                                   f"recovered after worker takeover while {st}; outcome unknown",
                                                   event_type=EventType.EFFECT_DISPATCH_UNKNOWN)
                elif st == E.COMPENSATING:
                    eff.restoration = str(Restoration.UNKNOWN)
                taken.append(eff.operation_key)
        if taken:
            append_event(s, rows.root, EventType.WORK_TAKEN_OVER, {
                "epoch": claim.epoch, "worker": claim.worker_id, "in_flight_effects": taken,
                "note": "reconcile before any replay"})
            if T(rows.root.state) in (T.COMMITTING, T.VERIFYING) and any(
                    E(e.state) == E.UNKNOWN for e in rows.effects.values()):
                self._move_tree(s, rows, T.UNKNOWN, "worker takeover found in-flight effects")

    # ============================================================== recovery
    async def _release_unsent(self, s: AsyncSession, rows: TreeRows, reason: str) -> list[str]:
        released = []
        for eff in rows.effects.values():
            if E(eff.state) in (E.PREPARED, E.RETRYABLE) and Application(eff.application) in (
                    Application.NOT_SENT, Application.NOT_APPLIED_CONFIRMED):
                lop = rows.logical_ops.get(eff.logical_operation_id) if eff.logical_operation_id else None
                if lop is not None and lop.owner_root_id == rows.root.id:
                    lop.status, lop.owner_root_id, lop.owner_effect_id = str(LogicalOperationStatus.AVAILABLE), None, None
                await self.ctx.evidence.release_unsent(s, eff, reason)
                self.manager.transition_effect(s, rows.txs[eff.transaction_id], eff, E.ABORTED,
                                               f"never dispatched; released ({reason})",
                                               event_type=EventType.EFFECT_RELEASED)
                released.append(eff.operation_key)
        return released

    async def _recovery_decision(self, s: AsyncSession, rows: TreeRows) -> None:
        with span("transaction.recovery", transaction_id=rows.root.id, root_id=rows.root.id):
            snap = rows.snapshot()
            graph = EffectGraph([e for e in snap.effects if e.state != E.ABORTED])
            evals = self.invariants.evaluate(snap, graph, InvariantPhase.POST_EXECUTION, self.manager.clock())
            self._persist_evaluations(s, rows, evals, "recovery")
            policy = self._policy(rows)
            failing = [ev for ev in evals if not ev.passed]
            human = policy.on_effect_failure == "HUMAN_REQUIRED" or any(
                ev.failure_action == FailureAction.HUMAN_REQUIRED for ev in failing)
            failed = [{"operation_key": e.operation_key, "effect_type": e.contract_type, "application": e.application,
                       "postcondition": e.postcondition, "dispatch": (e.dispatch_result or {}).get("outcome"),
                       "error": (e.dispatch_result or {}).get("error")}
                      for e in rows.effects.values() if E(e.state) == E.FAILED]
            abort = (rows.root.meta or {}).get("abort_requested")
            released = await self._release_unsent(s, rows, "recovery")
            await self.compensator.mark_scope(s, rows)
            action = "HUMAN_REQUIRED" if human else "RESTORE"
            append_event(s, rows.root, EventType.RECOVERY_DECISION, {
                "failed_effects": failed, "action": action, "released_effects": released, "abort_requested": abort,
                "policy": policy.model_dump(),
                "invariants": [{"key": ev.invariant_key, "passed": ev.passed, "reason": ev.reason,
                                "failure_action": str(ev.failure_action)} for ev in evals]})
            if action == "RESTORE":
                self._move_tree(s, rows, T.COMPENSATING, "restoring applied effects")
            else:
                self._move_tree(s, rows, T.HUMAN_REQUIRED, "recovery policy requires a human")

    async def _finish_restoration(self, s: AsyncSession, rows: TreeRows) -> None:
        open_blocking = [r for r in await self.ctx.evidence.open_residuals(s, rows.root.id)]
        effects = list(rows.effects.values())
        applied = [e for e in effects if Application(e.application) == Application.APPLIED]
        clean = (all(e.restoration == Restoration.RESTORED for e in applied)
                 and not any(Application(e.application) == Application.UNKNOWN and E(e.state) != E.ABORTED for e in effects)
                 and not open_blocking)
        append_event(s, rows.root, EventType.COMPENSATION_FINISHED, {
            "restored": [e.operation_key for e in applied if e.restoration == Restoration.RESTORED],
            "residual": [e.operation_key for e in applied if e.restoration != Restoration.RESTORED],
            "open_blocking_residuals": [r.kind for r in open_blocking], "clean": clean})
        if clean:
            self._move_tree(s, rows, T.COMPENSATED, "every applied effect restored and verified; no residual remains")
            await self._finalize_rows(s, rows)
        else:
            self._move_tree(s, rows, T.HUMAN_REQUIRED,
                            "restoration incomplete: residual consequences remain (see residual obligations)")

    async def _final_verification(self, root_id: UUID, claim: Claim | None) -> float | None:
        with span("transaction.final_verification", transaction_id=root_id, root_id=root_id):
            async with self.db.uow() as s:
                await self.ctx.queue.fence(s, claim)
                rows = await load_tree(s, root_id, lock=True)
                if rows.root.state == T.COMMITTING:
                    self._move_tree(s, rows, T.VERIFYING, "all effects verified; final transaction-level verification")
                append_event(s, rows.root, EventType.FINAL_VERIFICATION_STARTED, {})
                views = [effect_view(e) for e in rows.effects.values() if E(e.state) == E.VERIFIED]
            observations = {}
            for v in sorted(views, key=lambda v: v.operation_key):
                observations[v.id] = (await self.verifier.observe_final(v))[0]
            async with self.db.uow() as s:
                await self.ctx.queue.fence(s, claim)
                rows = await load_tree(s, root_id, lock=True)
                root = rows.root
                final_ids, drift = [], []
                rev = await self._frozen_revision(s, root)
                required_outcomes = (rev.compiled or {}).get("outcomes", []) if rev else []
                for eid, obs in observations.items():
                    eff = rows.effects[eid]
                    row = self.ctx.evidence.record_observation(s, eff, obs, ObservationPurpose.FINAL)
                    await s.flush()
                    final_ids.append(str(row.id))
                    # The final invariants and the receipt both read this exact observation (A29).
                    eff.verification_result = {**(eff.verification_result or {}), "status": str(obs.verification_status),
                                               "postcondition": str(obs.postcondition), "evidence": obs.evidence,
                                               "observation_id": str(row.id), "final": True, "reason": obs.reason}
                    eff.postcondition = str(obs.postcondition)
                    if obs.postcondition != Postcondition.MATCH:
                        drift.append(eff.operation_key)
                        self.ctx.evidence.open_residual(
                            s, rows.txs[eff.transaction_id], eff, ResidualKind.APPLIED_MISMATCH,
                            f"state no longer matches at final verification: {obs.reason}",
                            "Investigate the later change; restore or accept.", observation_id=row.id)
                    for requirement in required_outcomes:
                        if requirement.get("effect_id") != str(eid):
                            continue
                        try:
                            outcome_passed, outcome_reason = evaluate_required_outcome(requirement, obs)
                        except ValidationFailed as exc:
                            outcome_passed, outcome_reason = False, f"{exc.code}: {exc}"
                        if not outcome_passed:
                            drift.append(eff.operation_key)
                            self.ctx.evidence.open_residual(
                                s, rows.txs[eff.transaction_id], eff, ResidualKind.APPLIED_MISMATCH,
                                outcome_reason,
                                "Investigate the pinned outcome and actual provider state.",
                                observation_id=row.id)
                root.meta = {**(root.meta or {}), "final_observation_ids": sorted(final_ids)}
                snap = rows.snapshot()
                graph = EffectGraph([e for e in snap.effects if e.state != E.ABORTED])
                evals = []
                for phase in (InvariantPhase.POST_EXECUTION, InvariantPhase.FINAL):
                    evals += self.invariants.evaluate(snap, graph, phase, self.manager.clock())
                self._persist_evaluations(s, rows, evals, "final_verification")
                failing = [ev for ev in evals if not ev.passed]
                open_blocking = await self.ctx.evidence.open_residuals(s, root.id)
                append_event(s, root, EventType.FINAL_VERIFICATION_FINISHED, {
                    "final_observation_ids": sorted(final_ids), "drift": drift,
                    "invariants": [{"key": ev.invariant_key, "passed": ev.passed} for ev in evals]})
                if not failing and not drift and not open_blocking:
                    self._move_tree(s, rows, T.COMMITTED_VERIFIED,
                                    "every required effect verified on a fresh observation set; final invariants hold")
                    await self._finalize_rows(s, rows)
                    return None
                if not drift and failing and all(ev.failure_action == FailureAction.COMPENSATE for ev in failing):
                    self._move_tree(s, rows, T.COMPENSATING, "final invariant failed; restoring",
                                    failing=[ev.invariant_key for ev in failing])
                    await self.compensator.mark_scope(s, rows)
                    return 0.0
                self._move_tree(s, rows, T.HUMAN_REQUIRED, "final verification failed",
                                drift=drift, failing=[ev.invariant_key for ev in failing])
                return None

    async def _finalize_rows(self, s: AsyncSession, rows: TreeRows) -> None:
        """Terminal state + reservation disposition + receipts, in the caller's transaction (A20)."""
        if T(rows.root.state) not in TERMINAL_TX_STATES:
            return
        open_effects = {r.effect_id for r in await self.ctx.evidence.open_residuals(s, rows.root.id)}
        open_effects |= {e.id for e in rows.effects.values() if Application(e.application) == Application.UNKNOWN
                         and E(e.state) != E.ABORTED}
        summary = await self.reservations.release(s, rows.root.id, open_effects, f"root {rows.root.state}")
        if summary["released"] or summary["retained"]:
            append_event(s, rows.root, EventType.RESERVATIONS_RELEASED, summary)
        for t in rows.ordered_txs():
            if t.parent_id is not None:
                t.meta = {**(t.meta or {}), "local_outcome": local_outcome(rows.effects_of(t.id))}
        for t in sorted(rows.ordered_txs(), key=lambda t: -t.depth):
            await self.receipts.finalize(s, rows, t.id)

    async def finalize(self, root_id: UUID) -> None:
        async with self.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            await self._finalize_rows(s, rows)

    # ===================================================== operator actions
    async def operator_action(self, p: Principal, root_id: UUID, req: OperatorActionRequest) -> dict[str, Any]:
        from app.security.principals import reject_forged

        reject_forged(p, operator_id=req.operator_id)
        if str(p.kind) != "OPERATOR":
            raise Forbidden("operator actions require an authenticated operator", code="OPERATOR_REQUIRED")
        p.require("op:attest" if req.action == "ATTEST_RESIDUAL" else "op:recover")
        async with self.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            root = rows.root
            if root.tenant_id != p.tenant_id:
                raise NotFound(f"transaction {root_id} not found", code="TRANSACTION_NOT_FOUND")
            st = T(root.state)
            await self.manager.record_operator_action(s, root, p, req.action, req.reason,
                                                      payload={"residual_id": req.residual_id,
                                                               "evidence_reference": req.evidence_reference})
            if req.action == "RECONCILE":
                if root.quarantined:
                    raise StateConflict("legacy transactions are quarantined; finalize them with an attestation",
                                        code="QUARANTINED")
                if st not in (T.UNKNOWN, T.HUMAN_REQUIRED) or not any(
                        E(e.state) in (E.UNKNOWN, E.HUMAN_REQUIRED) and e.compensation_result is None
                        for e in rows.effects.values()):
                    raise StateConflict(f"nothing to reconcile in state {st}", code="NOTHING_TO_RECONCILE")
                for e in rows.effects.values():
                    if E(e.state) == E.HUMAN_REQUIRED and e.compensation_result is None:
                        self.manager.transition_effect(s, rows.txs[e.transaction_id], e, E.RECONCILING,
                                                       f"operator {p.name} requested reconciliation")
                root.meta = {**(root.meta or {}), "reconcile_rounds": 0}
                self._move_tree(s, rows, T.RECONCILING, f"operator {p.name}: {req.reason}")
                await self.ctx.queue.enqueue(s, root.tenant_id, root.id, WorkKind.DRIVE, reason="operator reconcile")
            elif req.action == "RETRY_RESTORATION":
                if st != T.HUMAN_REQUIRED:
                    raise StateConflict(f"restoration can be retried only from HUMAN_REQUIRED (is {st})",
                                        code="COMPENSATION_NOT_PERMITTED")
                if any(E(e.state) == E.UNKNOWN or (E(e.state) == E.HUMAN_REQUIRED and e.compensation_result is None)
                       for e in rows.effects.values()):
                    raise StateConflict("unresolved UNKNOWN effects must be reconciled before restoration",
                                        code="BLOCKING_UNKNOWN")
                for e in rows.effects.values():
                    if E(e.state) == E.HUMAN_REQUIRED and e.restoration in (Restoration.PENDING, Restoration.UNKNOWN):
                        self.manager.transition_effect(s, rows.txs[e.transaction_id], e, E.COMPENSATING,
                                                       f"operator {p.name} retries restoration")
                self._move_tree(s, rows, T.COMPENSATING, f"operator {p.name}: {req.reason}")
                await self.ctx.queue.enqueue(s, root.tenant_id, root.id, WorkKind.DRIVE, reason="operator restore")
            elif req.action == "ATTEST_RESIDUAL":
                r = await s.get(ResidualObligationRow, UUID(req.residual_id)) if req.residual_id else None
                if r is None or r.root_id != root.id or r.disposition != ResidualDisposition.OPEN:
                    raise ValidationFailed("residual_id must reference an OPEN residual of this transaction",
                                           code="INVALID_RESIDUAL")
                r.disposition, r.resolved_at = str(ResidualDisposition.ATTESTED), self.manager.clock()
                r.resolution = {"attested_by": p.name, "principal_id": str(p.id), "reason": req.reason,
                                "evidence_reference": req.evidence_reference,
                                "note": "human attestation - not provider-verified"}
                eff = rows.effects[r.effect_id]
                if r.kind in (ResidualKind.COMPENSATION_FAILED, ResidualKind.COMPENSATION_UNKNOWN,
                              ResidualKind.STALE_RESTORATION_BLOCKED, ResidualKind.IRREVERSIBLE_CONSEQUENCE):
                    eff.restoration = str(Restoration.HUMAN_ATTESTED)
                append_event(s, root, EventType.RESIDUAL_RESOLVED, {"residual_id": str(r.id), "kind": r.kind,
                                                                    "disposition": "ATTESTED", "by": p.name,
                                                                    "reason": req.reason}, effect_id=eff.id,
                             actor=f"operator:{p.name}")
                receipt = (await s.execute(select(ReceiptRow).where(ReceiptRow.transaction_id == root.id))).scalar_one_or_none()
                if receipt is not None:  # finalized earlier: link new evidence, never rewrite (A31)
                    await self.receipts.amend(s, receipt, "residual attested by operator",
                                              {"residual_id": str(r.id), "kind": r.kind, "attested_by": p.name,
                                               "reason": req.reason, "evidence_reference": req.evidence_reference})
                    append_event(s, root, EventType.RECEIPT_AMENDED, {"residual_id": str(r.id)})
            elif req.action == "FINALIZE_FAILED":
                if st != T.HUMAN_REQUIRED and not (root.quarantined and st not in TERMINAL_TX_STATES):
                    raise StateConflict(f"finalize-as-failed needs HUMAN_REQUIRED (is {st})", code="NOT_AWAITING_OPERATOR")
                for t in rows.ordered_txs():
                    if T(t.state) not in TERMINAL_TX_STATES and T.FAILED_TERMINAL not in TX_TRANSITIONS[T(t.state)]:
                        if T(t.state) in (T.UNKNOWN,):
                            self.manager.transition_tx(s, t, T.HUMAN_REQUIRED, "operator finalization")
                self._move_tree(s, rows, T.FAILED_TERMINAL, f"finalized by operator {p.name}: {req.reason}",
                                actor=f"operator:{p.name}")
                await self._finalize_rows(s, rows)
            return {"transaction_id": str(root.id), "state": root.state, "action": req.action}

    # ================================================================= sweeper
    async def sweep(self, *, prepare_stall_s: float = 60.0, stranded_grace_s: float = 2.0) -> dict[str, Any]:
        """Continuously repair stranded work (not only at startup)."""
        report = {"enqueued": [], "prepare_reset": [], "deadline": []}
        async with self.db.uow() as s:
            stranded = (await s.execute(text("""
                SELECT t.id, t.tenant_id, t.state FROM transactions t
                WHERE t.parent_id IS NULL AND NOT t.quarantined
                  AND (t.state IN ('COMMITTING','VERIFYING','RECONCILING','COMPENSATING','ABORTING')
                       OR (t.state IN ('COMMITTED_VERIFIED','ABORTED','COMPENSATED','FAILED_TERMINAL')
                           AND NOT EXISTS (SELECT 1 FROM receipts r WHERE r.transaction_id = t.id))
                       OR (t.state = 'UNKNOWN' AND coalesce((t.policy->>'auto_reconcile')::boolean, false)
                           AND coalesce((t.metadata->>'reconcile_rounds')::int, 0)
                               < coalesce((t.policy->>'max_auto_reconcile_rounds')::int, 0)))
                  AND t.updated_at < now() - make_interval(secs => :grace)
                  AND NOT EXISTS (SELECT 1 FROM work_items w WHERE w.root_id = t.id
                                  AND w.status IN ('READY', 'CLAIMED'))
                  AND NOT EXISTS (SELECT 1 FROM work_items w WHERE w.root_id = t.id AND w.status = 'FAILED'
                                  AND w.updated_at > t.updated_at)
            """), {"grace": stranded_grace_s})).all()
            for rid, tenant, state in stranded:
                await self.ctx.queue.enqueue(s, tenant, rid, WorkKind.DRIVE, reason=f"sweeper: stranded {state}")
                report["enqueued"].append(str(rid))
            stalls = (await s.execute(text("""
                SELECT id, root_id FROM transactions WHERE state = 'PREPARING'
                  AND updated_at < now() - make_interval(secs => :stall) AND NOT quarantined
            """), {"stall": prepare_stall_s})).all()
            failed_items = (await s.execute(text("""
                SELECT w.root_id, w.last_error FROM work_items w JOIN transactions t ON t.id = w.root_id
                WHERE w.status = 'FAILED' AND t.state NOT IN
                  ('HUMAN_REQUIRED','COMMITTED_VERIFIED','ABORTED','COMPENSATED','FAILED_TERMINAL')
                  AND w.updated_at > t.updated_at
            """))).all()
        for tx_id, root_id in stalls:
            async with self.db.uow() as s:
                rows = await load_tree(s, root_id, lock=True)
                tx = rows.txs[tx_id]
                cutoff = self.manager.clock() - timedelta(seconds=prepare_stall_s)
                if T(tx.state) == T.PREPARING and tx.updated_at < cutoff:
                    tx.prepare_generation += 1
                    self.manager.transition_tx(s, tx, T.SPECIFYING,
                                               "stalled read-only preparation reset by the sweeper")
                    report["prepare_reset"].append(str(tx_id))
        for rid, err in failed_items:
            async with self.db.uow() as s:
                rows = await load_tree(s, rid, lock=True)
                append_event(s, rows.root, EventType.RECOVERY_DEADLINE_EXCEEDED, {"error": err})
                if T.HUMAN_REQUIRED in TX_TRANSITIONS[T(rows.root.state)]:
                    self._move_tree(s, rows, T.HUMAN_REQUIRED, f"automatic recovery exhausted: {err}")
                report["deadline"].append(str(rid))
        return report


def _jsonable(obj: Any) -> Any:
    from app.domain.receipt import normalized

    return normalized(obj)
