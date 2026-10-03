"""Receipt generator.

Builds the canonical, hashable record of a finalized transaction: what was
attempted, who had authority, what was proposed, decided, executed, verified,
reconciled and compensated, and what remains un-restituted. Receipts are
written once (UNIQUE(transaction_id); DB trigger rejects UPDATE/DELETE).
A root receipt embeds the receipt hashes of its children.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import EffectRegistry
from app.core.state_machine import TERMINAL_TX_STATES
from app.domain.enums import EffectState, EventType, TransactionState
from app.domain.receipt import RECEIPT_VERSION, normalized, receipt_digest
from app.persistence.models import (
    CommitDecisionRow,
    InvariantEvaluationRow,
    InvariantRow,
    OperationAttemptRow,
    OperatorActionRow,
    ReceiptRow,
    TransactionEventRow,
)
from app.persistence.repositories import TreeRows, append_event, iso
from app.telemetry.tracing import span


def _amount(v: Any) -> str | None:
    return None if v is None else str(v)


class ReceiptGenerator:
    def __init__(self, registry: EffectRegistry):
        self.registry = registry

    async def build_payload(self, s: AsyncSession, rows: TreeRows, tx_id: UUID) -> dict[str, Any]:
        snap = rows.snapshot()
        tx = rows.txs[tx_id]
        sub_ids = snap.subtree_ids(tx_id)
        txs = [t for t in rows.ordered_txs() if t.id in sub_ids]
        effects = sorted((e for e in rows.effects.values() if e.transaction_id in sub_ids),
                         key=lambda e: (e.operation_key, str(e.id)))
        effect_ids = [e.id for e in effects]
        by_id = {e.id: e for e in effects}

        attempts = list((await s.execute(
            select(OperationAttemptRow).where(OperationAttemptRow.effect_id.in_(effect_ids))
            .order_by(OperationAttemptRow.started_at, OperationAttemptRow.kind, OperationAttemptRow.attempt_no)
        )).scalars()) if effect_ids else []

        inv_rows = {i.id: i for i in (await s.execute(
            select(InvariantRow).where(InvariantRow.transaction_id.in_(sub_ids)))).scalars()}
        evals = list((await s.execute(
            select(InvariantEvaluationRow).where(InvariantEvaluationRow.transaction_id.in_(sub_ids))
            .order_by(InvariantEvaluationRow.created_at)
        )).scalars())
        latest: dict[tuple, InvariantEvaluationRow] = {}
        for ev in evals:  # keep the latest evaluation per (invariant, phase, context)
            latest[(ev.invariant_id, ev.phase, ev.context)] = ev

        decision = (await s.execute(
            select(CommitDecisionRow).where(CommitDecisionRow.transaction_id == tx.root_id)
            .order_by(CommitDecisionRow.evaluated_at.desc()).limit(1)
        )).scalar_one_or_none()
        actions = list((await s.execute(
            select(OperatorActionRow).where(OperatorActionRow.transaction_id.in_(sub_ids))
            .order_by(OperatorActionRow.created_at)
        )).scalars())
        child_receipts = {r.transaction_id: r.sha256 for r in (await s.execute(
            select(ReceiptRow).where(ReceiptRow.transaction_id.in_(sub_ids)))).scalars()}
        event_count = (await s.execute(
            select(func.count()).select_from(TransactionEventRow)
            .where(TransactionEventRow.transaction_id.in_(sub_ids)))).scalar_one()

        def contract_of(effect_type: str):
            return self.registry.contract(effect_type) if self.registry.has(effect_type) else None

        final = TransactionState(tx.state)
        uncompensated = []
        for e in effects:
            st = EffectState(e.state)
            if final != TransactionState.COMMITTED_VERIFIED and st in (EffectState.VERIFIED, EffectState.HUMAN_REQUIRED, EffectState.UNKNOWN):
                uncompensated.append({
                    "operation_key": e.operation_key, "effect_type": e.contract_type, "state": e.state,
                    "reversibility_class": e.reversibility_class,
                    "reason": "irreversible effect remains committed" if st == EffectState.VERIFIED
                    else "outcome or restitution unresolved",
                })

        caps = [rows.caps[t.capability_id] for t in txs if t.capability_id in rows.caps]
        payload: dict[str, Any] = {
            "receipt_version": RECEIPT_VERSION,
            "transaction_id": tx.id,
            "root_id": tx.root_id,
            "parent_id": tx.parent_id,
            "objective": tx.objective,
            "initiator": tx.actor_id,
            "participants": sorted({t.actor_id for t in txs}),
            "created_at": tx.created_at,
            "finalized_at": tx.finalized_at,
            "final_state": tx.state,
            "outcome_statement": self._statement(final, effects, uncompensated),
            "capability_summary": [{
                "capability_id": c.id, "subject_id": c.subject_id, "issuer": c.issuer,
                "parent_capability_id": c.parent_capability_id,
                "allowed_effect_types": c.scope["allowed_effect_types"],
                "allowed_resources": c.scope["allowed_resources"],
                "amount_limit": _amount(c.amount_limit), "cumulative_amount_limit": _amount(c.cumulative_limit),
                "delegation_depth": c.delegation_depth, "expires_at": iso(c.expires_at),
            } for c in caps],
            "children": [{"transaction_id": t.id, "actor_id": t.actor_id, "final_state": t.state,
                          "receipt_hash": child_receipts.get(t.id)} for t in txs if t.parent_id == tx.id],
            "effects": [{
                "effect_id": e.id, "transaction_id": e.transaction_id, "actor_id": e.actor_id,
                "effect_type": e.contract_type, "operation_key": e.operation_key, "final_state": e.state,
                "reversibility_class": e.reversibility_class, "amount": _amount(e.amount),
                "payload": e.payload, "depends_on": e.depends_on, "provider_reference": e.provider_reference,
            } for e in effects],
            "invariant_results": sorted([{
                "key": inv_rows[ev.invariant_id].key, "name": inv_rows[ev.invariant_id].name, "phase": ev.phase,
                "context": ev.context, "passed": ev.passed, "reason": ev.reason,
                "failure_action": inv_rows[ev.invariant_id].failure_action, "observed": ev.observed_values,
            } for ev in latest.values() if ev.invariant_id in inv_rows],
                key=lambda r: (r["key"], r["phase"], r["context"])),
            "commit_decision": None if decision is None else {
                "eligible": decision.eligible, "evaluated_at": decision.evaluated_at,
                "blocking_reasons": decision.blocking_reasons,
                "checks": [{"code": c["code"], "subject": c.get("subject"), "passed": c["passed"]}
                           for c in decision.checks],
            },
            "resource_claims": sorted([{"operation_key": e.operation_key, **c} for e in effects
                                       for c in e.resource_claims],
                                      key=lambda r: (r["operation_key"], r["resource"])),
            "execution_results": [self._attempt(a, by_id) for a in attempts if a.kind == "EXECUTE"],
            "verification_results": [{
                "operation_key": e.operation_key, "dispatch_outcome": (e.dispatch_result or {}).get("outcome"),
                "verification_status": (e.verification_result or {}).get("status"),
                "verification_source": (e.verification_result or {}).get("source"),
                "external_reference": (e.verification_result or {}).get("external_reference"),
                "evidence": (e.verification_result or {}).get("evidence"),
                "verified_at": iso(e.verified_at),
            } for e in effects if e.verification_result or e.dispatch_result],
            "reconciliation_events": [
                {**self._attempt(a, by_id), "outcome": (by_id[a.effect_id].reconciliation_result or {}).get("outcome")}
                for a in attempts if a.kind == "RECONCILE"],
            "compensation_results": [self._attempt(a, by_id) for a in attempts if a.kind == "COMPENSATE"],
            "uncompensated_effects": uncompensated,
            "human_actions": [{"operator_id": a.operator_id, "action": a.action, "note": a.note,
                               "effect_id": a.effect_id, "at": a.created_at} for a in actions],
            "external_references": sorted([{
                "operation_key": e.operation_key,
                "system": (contract_of(e.contract_type).adapter_name if contract_of(e.contract_type) else None),
                "reference": e.provider_reference,
            } for e in effects if e.provider_reference], key=lambda r: r["operation_key"]),
            "event_count": int(event_count),
        }
        return normalized(payload)

    @staticmethod
    def _attempt(a: OperationAttemptRow, by_id: dict) -> dict[str, Any]:
        resp = a.response or {}
        return {"operation_key": by_id[a.effect_id].operation_key, "kind": a.kind, "attempt_no": a.attempt_no,
                "status": a.status, "http_status": resp.get("http_status"),
                "error": (a.error or {}).get("message"), "started_at": a.started_at, "finished_at": a.finished_at}

    @staticmethod
    def _statement(final: TransactionState, effects, uncompensated) -> str:
        n = len(effects)
        count = lambda st: sum(1 for e in effects if e.state == st)  # noqa: E731
        if final == TransactionState.COMMITTED_VERIFIED:
            return f"All {n} effects were executed and independently verified against external state."
        if final == TransactionState.ABORTED:
            return f"Transaction aborted before any external effect was dispatched; {n} proposed effects were released."
        if final == TransactionState.COMPENSATED:
            return (f"Transaction did not complete. {count('COMPENSATED')} committed effect(s) were compensated and the "
                    f"compensation verified; {count('FAILED')} failed; {count('ABORTED')} were never dispatched. "
                    "No committed effect remains un-restituted.")
        return (f"Transaction ended {final}. {len(uncompensated)} effect(s) remain committed or unresolved and require "
                f"accountable follow-up; {count('COMPENSATED')} were compensated.")

    async def finalize(self, s: AsyncSession, rows: TreeRows, tx_id: UUID) -> ReceiptRow | None:
        tx = rows.txs[tx_id]
        if TransactionState(tx.state) not in TERMINAL_TX_STATES:
            return None
        existing = (await s.execute(select(ReceiptRow).where(ReceiptRow.transaction_id == tx_id))).scalar_one_or_none()
        if existing is not None:
            return existing  # receipts are immutable; finalization is idempotent
        with span("receipt.finalize", transaction_id=tx_id, root_id=tx.root_id, state=tx.state):
            payload = await self.build_payload(s, rows, tx_id)
            digest = receipt_digest(payload)
            payload["receipt_hash"] = digest
            row = ReceiptRow(transaction_id=tx_id, root_id=tx.root_id, receipt_version=RECEIPT_VERSION,
                             final_state=tx.state, payload=payload, sha256=digest)
            s.add(row)
            await s.flush()
            append_event(s, tx, EventType.TRANSACTION_FINALIZED, {"final_state": tx.state})
            append_event(s, tx, EventType.RECEIPT_CREATED, {"receipt_hash": digest, "receipt_version": RECEIPT_VERSION})
            return row
