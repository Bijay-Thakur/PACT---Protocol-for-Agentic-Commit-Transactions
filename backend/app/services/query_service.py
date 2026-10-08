"""Read models for the API and console. Strictly read-only - never mutates state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select

from app.core.effect_graph import EffectGraph
from app.core.state_machine import TERMINAL_TX_STATES
from app.domain.enums import TransactionState
from app.domain.errors import NotFound, ReceiptNotFinal
from app.domain.receipt import receipt_digest
from app.persistence.models import (
    ApprovalRow,
    CommitDecisionRow,
    EffectRow,
    InvariantEvaluationRow,
    OperationAttemptRow,
    OperatorActionRow,
    PlanRevisionRow,
    ReceiptRow,
    ResidualObligationRow,
    TransactionEventRow,
    TransactionRow,
    WorkItemRow,
)
from app.persistence.repositories import get_tx, iso, load_tree
from app.runtime import Runtime
from app.security.principals import Principal


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


class QueryService:
    def __init__(self, rt: Runtime):
        self.rt = rt
        self.db = rt.db

    async def list_transactions(
        self,
        p: Principal,
        limit: int = 50,
        *,
        state: str | None = None,
        workflow: str | None = None,
        actor: str | None = None,
        search: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        before_created_at: datetime | None = None,
        before_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
        async with self.db.read() as s:
            visible_roots = select(TransactionRow.root_id).where(TransactionRow.principal_id == p.id)
            stmt = select(TransactionRow).where(TransactionRow.parent_id.is_(None),
                                                TransactionRow.tenant_id == p.tenant_id)
            if not p.has("tx:read_all"):
                stmt = stmt.where(TransactionRow.id.in_(visible_roots))
            if state:
                stmt = stmt.where(TransactionRow.state == state)
            if workflow:
                stmt = stmt.where(TransactionRow.workflow == workflow)
            if actor:
                stmt = stmt.where(TransactionRow.actor_id == actor)
            if search:
                escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                stmt = stmt.where(or_(
                    TransactionRow.objective.ilike(f"%{escaped}%", escape="\\"),
                    TransactionRow.business_request_key.ilike(f"%{escaped}%", escape="\\"),
                ))
            if created_after is not None:
                stmt = stmt.where(TransactionRow.created_at >= created_after)
            if created_before is not None:
                stmt = stmt.where(TransactionRow.created_at < created_before)
            if before_created_at is not None and before_id is not None:
                stmt = stmt.where(or_(
                    TransactionRow.created_at < before_created_at,
                    and_(
                        TransactionRow.created_at == before_created_at,
                        TransactionRow.id < before_id,
                    ),
                ))
            roots = list((await s.execute(
                stmt.order_by(TransactionRow.created_at.desc(), TransactionRow.id.desc()).limit(limit)
            )).scalars())
            if not roots:
                return []
            ids = [r.id for r in roots]
            child_counts = dict((await s.execute(
                select(TransactionRow.root_id, func.count()).where(
                    TransactionRow.root_id.in_(ids), TransactionRow.parent_id.is_not(None))
                .group_by(TransactionRow.root_id))).all())
            effect_counts = dict((await s.execute(
                select(EffectRow.root_id, func.count()).where(EffectRow.root_id.in_(ids))
                .group_by(EffectRow.root_id))).all())
            tx_roots = dict((await s.execute(
                select(TransactionRow.id, TransactionRow.root_id).where(TransactionRow.root_id.in_(ids)))).all())
            evals = (await s.execute(
                select(InvariantEvaluationRow.transaction_id, InvariantEvaluationRow.passed)
                .where(InvariantEvaluationRow.transaction_id.in_(list(tx_roots))))).all()
            inv_status: dict[UUID, str] = {}
            for tx_id, passed in evals:
                rid = tx_roots[tx_id]
                if not passed:
                    inv_status[rid] = "FAILED"
                else:
                    inv_status.setdefault(rid, "PASSED")
            return [{
                "id": str(r.id), "objective": r.objective, "state": r.state, "actor_id": r.actor_id,
                "child_count": child_counts.get(r.id, 0), "effect_count": effect_counts.get(r.id, 0),
                "invariant_status": inv_status.get(r.id, "NOT_EVALUATED"),
                "scenario": (r.meta or {}).get("scenario"), "customer_id": (r.meta or {}).get("customer_id"),
                "created_at": iso(r.created_at), "updated_at": iso(r.updated_at), "finalized_at": iso(r.finalized_at),
            } for r in roots]

    async def operations_overview(self, p: Principal) -> dict[str, Any]:
        """Tenant-wide counts; never approximated from a recent-row list."""
        async with self.db.read() as s:
            visible = [TransactionRow.tenant_id == p.tenant_id, TransactionRow.parent_id.is_(None)]
            if not p.has("tx:read_all"):
                visible.append(TransactionRow.principal_id == p.id)
            grouped = dict((await s.execute(
                select(TransactionRow.state, func.count()).where(*visible)
                .group_by(TransactionRow.state)
            )).all())
            root_ids = select(TransactionRow.id).where(*visible)
            incidents = (await s.execute(
                select(func.count()).select_from(TransactionRow).where(
                    TransactionRow.id.in_(root_ids),
                    TransactionRow.state.in_(["UNKNOWN", "HUMAN_REQUIRED"]),
                )
            )).scalar_one()
            residuals = (await s.execute(
                select(func.count()).select_from(ResidualObligationRow).where(
                    ResidualObligationRow.tenant_id == p.tenant_id,
                    ResidualObligationRow.disposition == "OPEN",
                    ResidualObligationRow.root_id.in_(root_ids),
                )
            )).scalar_one()
            backlog = (await s.execute(
                select(func.count()).select_from(WorkItemRow).where(
                    WorkItemRow.tenant_id == p.tenant_id,
                    WorkItemRow.status.in_(["READY", "CLAIMED", "FAILED"]),
                    WorkItemRow.root_id.in_(root_ids),
                )
            )).scalar_one()
            pending_approvals = (await s.execute(
                select(func.count()).select_from(PlanRevisionRow)
                .join(TransactionRow, TransactionRow.id == PlanRevisionRow.root_id)
                .where(
                    *visible,
                    PlanRevisionRow.revision_no == TransactionRow.current_revision,
                    PlanRevisionRow.status == "FROZEN",
                    PlanRevisionRow.approval_required.is_(True),
                    ~select(ApprovalRow.id).where(
                        ApprovalRow.revision_id == PlanRevisionRow.id,
                        ApprovalRow.digest == PlanRevisionRow.digest,
                        ApprovalRow.revoked_at.is_(None),
                    ).exists(),
                )
            )).scalar_one()
        terminal = {str(state) for state in TERMINAL_TX_STATES}
        return {
            "tenant_id": p.tenant_id,
            "principal": p.name,
            "roles": p.roles,
            "state_counts": grouped,
            "active": sum(count for state, count in grouped.items() if state not in terminal),
            "incidents": incidents,
            "open_residuals": residuals,
            "pending_approvals": pending_approvals,
            "worker_backlog": backlog,
            "measured_at": iso(datetime.now(UTC)),
        }

    async def approval_queue(self, p: Principal, limit: int = 100) -> list[dict[str, Any]]:
        p.require("op:approve")
        async with self.db.read() as s:
            rows = (await s.execute(
                select(TransactionRow, PlanRevisionRow)
                .join(PlanRevisionRow, PlanRevisionRow.root_id == TransactionRow.id)
                .where(
                    TransactionRow.tenant_id == p.tenant_id,
                    TransactionRow.parent_id.is_(None),
                    TransactionRow.state == "PREPARED",
                    PlanRevisionRow.revision_no == TransactionRow.current_revision,
                    PlanRevisionRow.status == "FROZEN",
                    PlanRevisionRow.approval_required.is_(True),
                )
                .order_by(TransactionRow.updated_at.asc(), TransactionRow.id.asc())
                .limit(limit)
            )).all()
        return [{
            "transaction_id": str(root.id),
            "objective": root.objective,
            "digest": revision.digest,
            "revision": revision.revision_no,
            "approval": (revision.compiled or {}).get("approval"),
            "projection": (revision.compiled or {}).get("projection"),
            "semantic_assessment": (revision.compiled or {}).get("semantic_assessment"),
            "updated_at": iso(root.updated_at),
        } for root, revision in rows
            if ((revision.compiled or {}).get("approval") or {}).get("role") in p.roles]

    async def incident_queue(self, p: Principal, limit: int = 100) -> list[dict[str, Any]]:
        p.require("tx:read_all")
        async with self.db.read() as s:
            roots = list((await s.execute(
                select(TransactionRow).where(
                    TransactionRow.tenant_id == p.tenant_id,
                    TransactionRow.parent_id.is_(None),
                    TransactionRow.state.in_(["UNKNOWN", "HUMAN_REQUIRED"]),
                ).order_by(TransactionRow.updated_at.asc(), TransactionRow.id.asc()).limit(limit)
            )).scalars())
            ids = [root.id for root in roots]
            residuals = list((await s.execute(
                select(ResidualObligationRow).where(
                    ResidualObligationRow.root_id.in_(ids),
                    ResidualObligationRow.disposition == "OPEN",
                )
            )).scalars()) if ids else []
        by_root: dict[UUID, list[dict[str, Any]]] = {}
        for item in residuals:
            by_root.setdefault(item.root_id, []).append({
                "id": str(item.id), "kind": item.kind, "description": item.description,
                "required_remediation": item.required_remediation,
            })
        return [{
            "transaction_id": str(root.id),
            "objective": root.objective,
            "state": root.state,
            "updated_at": iso(root.updated_at),
            "residuals": by_root.get(root.id, []),
            "permitted_actions": (
                ["RECONCILE", "FINALIZE_FAILED"] if root.state == "UNKNOWN"
                else ["RECONCILE", "RETRY_RESTORATION", "ATTEST_RESIDUAL", "FINALIZE_FAILED"]
            ),
        } for root in roots]

    async def detail(self, p: Principal, tx_id: UUID) -> dict[str, Any]:
        async with self.db.read() as s:
            tx = await get_tx(s, tx_id)
            await self.rt.manager.assert_can_read(s, p, tx.root_id)
            rows = await load_tree(s, tx.root_id, lock=False)
            revision = (await s.execute(select(PlanRevisionRow).where(
                PlanRevisionRow.root_id == tx.root_id,
                PlanRevisionRow.revision_no == rows.root.current_revision))).scalar_one_or_none()
            snap = rows.snapshot()
            effect_ids = list(rows.effects)
            attempts = list((await s.execute(
                select(OperationAttemptRow).where(OperationAttemptRow.effect_id.in_(effect_ids))
                .order_by(OperationAttemptRow.started_at))).scalars()) if effect_ids else []
            evals = list((await s.execute(
                select(InvariantEvaluationRow).where(InvariantEvaluationRow.transaction_id.in_(list(rows.txs)))
                .order_by(InvariantEvaluationRow.created_at))).scalars())
            decisions = list((await s.execute(
                select(CommitDecisionRow).where(CommitDecisionRow.transaction_id == tx.root_id)
                .order_by(CommitDecisionRow.evaluated_at.desc()))).scalars())
            actions = list((await s.execute(
                select(OperatorActionRow).where(OperatorActionRow.transaction_id.in_(list(rows.txs)))
                .order_by(OperatorActionRow.created_at))).scalars())
            receipts = {r.transaction_id: r.sha256 for r in (await s.execute(
                select(ReceiptRow).where(ReceiptRow.root_id == tx.root_id))).scalars()}
            event_count = (await s.execute(select(func.count()).select_from(TransactionEventRow)
                                           .where(TransactionEventRow.root_id == tx.root_id))).scalar_one()

        graph = EffectGraph(snap.effects)
        gv = graph.validate()
        levels = graph.levels() if not gv.cycles else {}
        by_effect: dict[UUID, list] = {}
        for a in attempts:
            by_effect.setdefault(a.effect_id, []).append({
                "kind": a.kind, "attempt_no": a.attempt_no, "status": a.status,
                "http_status": (a.response or {}).get("http_status"), "error": (a.error or {}).get("message"),
                "started_at": iso(a.started_at), "finished_at": iso(a.finished_at),
            })
        exposure = {str(r.capability_id): {"exposure": str(r.exposure), "limit": _s(r.limit), "passed": r.passed,
                                           "contributions": r.contributions}
                    for r in self.rt.authority.cumulative_exposure(snap)}

        def contract(effect_type: str) -> dict | None:
            return self.rt.registry.contract(effect_type).public() if self.rt.registry.has(effect_type) else None

        root = rows.root
        dry_run = None
        if TransactionState(root.state) in (TransactionState.SPECIFYING, TransactionState.PREPARING,
                                            TransactionState.PREPARED, TransactionState.CREATED):
            dry_run = self.rt.barrier.evaluate(snap, self.rt.manager.clock(), binding=False).decision.model_dump(mode="json")

        return {
            "transaction": self._tx(tx, rows, receipts),
            "root_id": str(root.id),
            "policy": root.policy,
            "plan_revision": None if revision is None else {
                "number": revision.revision_no, "status": revision.status,
                "digest": revision.digest, "approval_required": revision.approval_required,
                "approval": (revision.compiled or {}).get("approval"),
                "projection": (revision.compiled or {}).get("projection"),
                "required_outcomes": (revision.compiled or {}).get("outcomes", []),
                "candidate_digest": revision.candidate_digest,
                "semantic_disposition": revision.semantic_disposition,
                "semantic_assessment": (revision.compiled or {}).get("semantic_assessment"),
                "issues": (revision.compile_result or {}).get("issues", []),
            },
            "metadata": root.meta,
            "tree": [self._tx(t, rows, receipts) for t in rows.ordered_txs()],
            "capabilities": [{
                "id": str(c.id), "transaction_id": str(c.transaction_id), "subject_id": c.subject_id,
                "issuer": c.issuer, "parent_capability_id": _s(c.parent_capability_id),
                "allowed_effect_types": c.scope["allowed_effect_types"],
                "allowed_resources": c.scope["allowed_resources"],
                "amount_limit": _s(c.amount_limit), "cumulative_amount_limit": _s(c.cumulative_limit),
                "delegation_depth": c.delegation_depth, "expires_at": iso(c.expires_at),
                "exposure": exposure.get(str(c.id)),
            } for c in sorted(rows.caps.values(), key=lambda c: (rows.txs[c.transaction_id].depth, c.subject_id))],
            "effects": [{
                "id": str(e.id), "transaction_id": str(e.transaction_id), "actor_id": e.actor_id,
                "effect_type": e.contract_type, "operation_key": e.operation_key, "state": e.state,
                "payload": e.payload, "amount": _s(e.amount), "resource_claims": e.resource_claims,
                "depends_on": e.depends_on, "reversibility_class": e.reversibility_class,
                "provider_idempotency_key": e.provider_idempotency_key, "provider_reference": e.provider_reference,
                "prepare_evidence": e.prepare_evidence, "dispatch_result": e.dispatch_result,
                "verification_result": e.verification_result, "reconciliation_result": e.reconciliation_result,
                "compensation_result": e.compensation_result, "dispatched_at": iso(e.dispatched_at),
                "verified_at": iso(e.verified_at), "level": levels.get(e.id),
                "logical_operation": {
                    "id": str(e.logical_operation_id),
                    "status": rows.logical_ops[e.logical_operation_id].status if e.logical_operation_id in rows.logical_ops else None,
                    "owner_root_id": _s(rows.logical_ops[e.logical_operation_id].owner_root_id) if e.logical_operation_id in rows.logical_ops else None,
                },
                "attempts": by_effect.get(e.id, []),
                "contract": contract(e.contract_type),
            } for e in sorted(rows.effects.values(), key=lambda e: (levels.get(e.id, 0), e.operation_key))],
            "graph": {
                "valid": gv.valid, "cycles": gv.cycles, "unresolved": gv.unresolved,
                "edges": [{"from": str(a), "to": str(b)} for a, b in graph.edges()],
            },
            "invariants": [{
                "id": str(i.id), "transaction_id": str(i.transaction_id), "key": i.key, "name": i.name,
                "phase": i.phase, "expression_type": i.expression_type, "config": i.definition,
                "failure_action": i.failure_action, "severity": i.severity,
                "evaluations": [{"context": ev.context, "phase": ev.phase, "passed": ev.passed, "reason": ev.reason,
                                 "observed_values": ev.observed_values, "at": iso(ev.created_at)}
                                for ev in evals if ev.invariant_id == i.id],
            } for i in sorted(rows.invariants, key=lambda i: (i.phase, i.key))],
            "commit_decisions": [{
                "id": str(d.id), "eligible": d.eligible, "binding": True, "snapshot_version": d.snapshot_version,
                "checks": d.checks, "blocking_reasons": d.blocking_reasons, "evaluated_at": iso(d.evaluated_at),
            } for d in decisions],
            "dry_run_decision": dry_run,
            "operator_actions": [{"operator_id": a.operator_id, "action": a.action, "note": a.note,
                                  "effect_id": _s(a.effect_id), "at": iso(a.created_at)} for a in actions],
            "receipts": [{"transaction_id": str(k), "sha256": v} for k, v in receipts.items()],
            "event_count": event_count,
        }

    def _tx(self, t: TransactionRow, rows, receipts: dict) -> dict[str, Any]:
        return {
            "id": str(t.id), "root_id": str(t.root_id), "parent_id": _s(t.parent_id), "depth": t.depth,
            "objective": t.objective, "actor_id": t.actor_id, "state": t.state, "required": t.required,
            "capability_id": _s(t.capability_id), "version": t.version,
            "effect_count": len(rows.effects_of(t.id)),
            "child_count": sum(1 for x in rows.txs.values() if x.parent_id == t.id),
            "created_at": iso(t.created_at), "updated_at": iso(t.updated_at), "finalized_at": iso(t.finalized_at),
            "receipt_hash": receipts.get(t.id),
        }

    async def events(self, p: Principal, tx_id: UUID, after: int = 0, limit: int = 500,
                     scope: str = "tree") -> list[dict[str, Any]]:
        async with self.db.read() as s:
            tx = await get_tx(s, tx_id)
            await self.rt.manager.assert_can_read(s, p, tx.root_id)
            stmt = select(TransactionEventRow).where(TransactionEventRow.sequence > after)
            stmt = stmt.where(TransactionEventRow.root_id == tx.root_id) if scope == "tree" else stmt.where(
                TransactionEventRow.transaction_id == tx_id)
            rows = (await s.execute(stmt.order_by(TransactionEventRow.sequence).limit(limit))).scalars()
            return [{"sequence": e.sequence, "id": str(e.id), "transaction_id": str(e.transaction_id),
                     "effect_id": _s(e.effect_id), "event_type": e.event_type, "actor": e.actor,
                     "payload": e.payload, "created_at": iso(e.created_at)} for e in rows]

    async def receipt(self, p: Principal, tx_id: UUID) -> dict[str, Any]:
        async with self.db.read() as s:
            tx = await get_tx(s, tx_id)
            await self.rt.manager.assert_can_read(s, p, tx.root_id)
            row = (await s.execute(select(ReceiptRow).where(ReceiptRow.transaction_id == tx_id))).scalar_one_or_none()
            if row is None:
                if TransactionState(tx.state) in TERMINAL_TX_STATES:
                    raise NotFound("receipt not yet generated", code="RECEIPT_PENDING")
                rows = await load_tree(s, tx.root_id, lock=False)
                draft = await self.rt.receipts.build_payload(s, rows, tx_id)
                raise ReceiptNotFinal(
                    f"transaction is {tx.state}; receipts are issued only for terminal states",
                    details={"state": tx.state, "draft": draft})
            return {"transaction_id": str(tx_id), "sha256": row.sha256, "receipt_version": row.receipt_version,
                    "final_state": row.final_state, "created_at": iso(row.created_at), "payload": row.payload}

    async def verify_receipt(self, p: Principal, tx_id: UUID) -> dict[str, Any]:
        r = await self.receipt(p, tx_id)
        recomputed = receipt_digest(r["payload"])
        return {"transaction_id": str(tx_id), "stored_sha256": r["sha256"], "recomputed_sha256": recomputed,
                "embedded_receipt_hash": r["payload"].get("receipt_hash"),
                "valid": recomputed == r["sha256"] == r["payload"].get("receipt_hash")}
