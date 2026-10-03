"""Read models for the API and console. Strictly read-only - never mutates state."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from app.core.effect_graph import EffectGraph
from app.core.state_machine import TERMINAL_TX_STATES
from app.domain.enums import TransactionState
from app.domain.errors import NotFound, ReceiptNotFinal
from app.domain.receipt import receipt_digest
from app.persistence.models import (
    CommitDecisionRow,
    EffectRow,
    InvariantEvaluationRow,
    OperationAttemptRow,
    OperatorActionRow,
    ReceiptRow,
    TransactionEventRow,
    TransactionRow,
)
from app.persistence.repositories import get_tx, iso, load_tree
from app.runtime import Runtime


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


class QueryService:
    def __init__(self, rt: Runtime):
        self.rt = rt
        self.db = rt.db

    async def list_transactions(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self.db.read() as s:
            roots = list((await s.execute(
                select(TransactionRow).where(TransactionRow.parent_id.is_(None))
                .order_by(TransactionRow.created_at.desc()).limit(limit))).scalars())
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

    async def detail(self, tx_id: UUID) -> dict[str, Any]:
        async with self.db.read() as s:
            tx = await get_tx(s, tx_id)
            rows = await load_tree(s, tx.root_id, lock=False)
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

    async def events(self, tx_id: UUID, after: int = 0, limit: int = 500, scope: str = "tree") -> list[dict[str, Any]]:
        async with self.db.read() as s:
            tx = await get_tx(s, tx_id)
            stmt = select(TransactionEventRow).where(TransactionEventRow.sequence > after)
            stmt = stmt.where(TransactionEventRow.root_id == tx.root_id) if scope == "tree" else stmt.where(
                TransactionEventRow.transaction_id == tx_id)
            rows = (await s.execute(stmt.order_by(TransactionEventRow.sequence).limit(limit))).scalars()
            return [{"sequence": e.sequence, "id": str(e.id), "transaction_id": str(e.transaction_id),
                     "effect_id": _s(e.effect_id), "event_type": e.event_type, "actor": e.actor,
                     "payload": e.payload, "created_at": iso(e.created_at)} for e in rows]

    async def receipt(self, tx_id: UUID) -> dict[str, Any]:
        async with self.db.read() as s:
            tx = await get_tx(s, tx_id)
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

    async def verify_receipt(self, tx_id: UUID) -> dict[str, Any]:
        r = await self.receipt(tx_id)
        recomputed = receipt_digest(r["payload"])
        return {"transaction_id": str(tx_id), "stored_sha256": r["sha256"], "recomputed_sha256": recomputed,
                "embedded_receipt_hash": r["payload"].get("receipt_hash"),
                "valid": recomputed == r["sha256"] == r["payload"].get("receipt_hash")}
