"""Receipt generator (``pact.receipt/v2``).

A receipt is the canonical, hashable record of a finalized transaction. v2 adds
what Phase 2 makes true: the frozen plan digest and revision, policy and contract
versions, verified principals and delegation, the binding barrier decision,
reservations and budget ledger entries, the three outcome dimensions per effect,
citations of the exact observation set used for final checks, uncertainty,
residual obligations (open, resolved, attested or accepted), skipped optional
effects, approvals and human attestations - kept distinct from provider evidence.

Receipts are written once (UNIQUE(transaction_id) + immutability trigger). Later
evidence is attached as hash-chained amendments, never by rewriting. The hash is
integrity metadata, not a signature, and does not by itself prove external truth.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import EffectRegistry
from app.core.state_machine import TERMINAL_TX_STATES
from app.domain.enums import Application, EffectState, EventType, Restoration, TransactionState
from app.domain.receipt import normalized, receipt_digest
from app.persistence.models import (
    ApprovalRow,
    BudgetEntryRow,
    BudgetScopeRow,
    CommitDecisionRow,
    InvariantEvaluationRow,
    InvariantRow,
    ObservationRow,
    OperationAttemptRow,
    OperatorActionRow,
    PlanRevisionRow,
    PrincipalRow,
    ReceiptAmendmentRow,
    ReceiptRow,
    ReservationRow,
    ResidualObligationRow,
    TransactionEventRow,
)
from app.persistence.repositories import TreeRows, append_event, iso
from app.telemetry.tracing import span

RECEIPT_VERSION_V2 = "pact.receipt/v2"
SECRET_KEY = re.compile(r"(secret|token|password|api[_-]?key|authorization|credential)", re.I)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if SECRET_KEY.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def _amount(v: Any) -> str | None:
    return None if v is None else str(v)


def local_outcome(effects) -> str:
    """Per-child truthful outcome (children are not assigned the root's story)."""
    if not effects:
        return "NO_EFFECTS"
    apps = {e.application for e in effects}
    if Application.UNKNOWN in apps and any(e.state != EffectState.VERIFIED for e in effects
                                           if e.application == Application.UNKNOWN):
        return "UNKNOWN"
    if all(e.state == EffectState.VERIFIED for e in effects):
        return "SATISFIED"
    if all(e.application in (Application.NOT_SENT, Application.NOT_APPLIED_CONFIRMED) for e in effects):
        return "NOT_EXECUTED"
    applied = [e for e in effects if e.application == Application.APPLIED]
    if applied and all(e.restoration == Restoration.RESTORED for e in applied):
        return "RESTORED"
    return "RESIDUAL"


class ReceiptGenerator:
    def __init__(self, registry: EffectRegistry):
        self.registry = registry

    async def build_payload(self, s: AsyncSession, rows: TreeRows, tx_id: UUID) -> dict[str, Any]:
        snap = rows.snapshot()
        tx = rows.txs[tx_id]
        root = rows.root
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
        latest: dict[tuple, InvariantEvaluationRow] = {}
        for ev in (await s.execute(select(InvariantEvaluationRow).where(
                InvariantEvaluationRow.transaction_id.in_(sub_ids)).order_by(InvariantEvaluationRow.created_at))).scalars():
            latest[(ev.invariant_id, ev.phase, ev.context)] = ev
        decision = (await s.execute(select(CommitDecisionRow).where(CommitDecisionRow.transaction_id == root.id)
                                    .order_by(CommitDecisionRow.evaluated_at.desc()).limit(1))).scalar_one_or_none()
        actions = list((await s.execute(select(OperatorActionRow).where(
            OperatorActionRow.transaction_id.in_(sub_ids)).order_by(OperatorActionRow.created_at))).scalars())
        child_receipts = {r.transaction_id: r.sha256 for r in (await s.execute(
            select(ReceiptRow).where(ReceiptRow.transaction_id.in_(sub_ids)))).scalars()}
        event_count = (await s.execute(select(func.count()).select_from(TransactionEventRow)
                                       .where(TransactionEventRow.transaction_id.in_(sub_ids)))).scalar_one()
        revision = (await s.execute(select(PlanRevisionRow).where(
            PlanRevisionRow.root_id == root.id, PlanRevisionRow.digest.is_not(None))
            .order_by(PlanRevisionRow.revision_no.desc()).limit(1))).scalar_one_or_none()
        approvals = list((await s.execute(select(ApprovalRow).where(ApprovalRow.root_id == root.id)
                                          .order_by(ApprovalRow.created_at))).scalars())
        residuals = list((await s.execute(select(ResidualObligationRow).where(
            ResidualObligationRow.effect_id.in_(effect_ids)).order_by(ResidualObligationRow.created_at)))
            .scalars()) if effect_ids else []
        reservations = list((await s.execute(select(ReservationRow).where(
            ReservationRow.effect_id.in_(effect_ids)).order_by(ReservationRow.resource_key)))
            .scalars()) if effect_ids else []
        budget = (await s.execute(select(BudgetEntryRow, BudgetScopeRow.key).join(
            BudgetScopeRow, BudgetScopeRow.id == BudgetEntryRow.scope_id).where(
            BudgetEntryRow.effect_id.in_(effect_ids)).order_by(BudgetEntryRow.created_at))).all() if effect_ids else []
        final_ids = [UUID(x) for x in (root.meta or {}).get("final_observation_ids", [])]
        final_obs = list((await s.execute(select(ObservationRow).where(
            ObservationRow.id.in_(final_ids), ObservationRow.effect_id.in_(effect_ids))))
            .scalars()) if final_ids and effect_ids else []
        all_obs = list((await s.execute(select(ObservationRow).where(ObservationRow.effect_id.in_(effect_ids))
                                        .order_by(ObservationRow.observed_at))).scalars()) if effect_ids else []
        principals = {p.id: p for p in (await s.execute(select(PrincipalRow).where(
            PrincipalRow.id.in_([t.principal_id for t in txs if t.principal_id])))).scalars()}

        final = TransactionState(tx.state)
        open_residuals = [r for r in residuals if r.disposition == "OPEN"]
        caps = [rows.caps[t.capability_id] for t in txs if t.capability_id in rows.caps]
        payload: dict[str, Any] = {
            "receipt_version": RECEIPT_VERSION_V2,
            "transaction_id": tx.id, "root_id": tx.root_id, "parent_id": tx.parent_id,
            "tenant_id": tx.tenant_id, "objective": tx.objective,
            "workflow": {"key": root.workflow, "version": root.workflow_version,
                         "policy_version": revision.policy_version if revision else None},
            "plan": {"revision_no": revision.revision_no if revision else None,
                     "digest": revision.digest if revision else None,
                     "status": revision.status if revision else None,
                     "approval_required": revision.approval_required if revision else None,
                     "skipped": (revision.compile_result or {}).get("skipped", []) if revision else []},
            "business_request_key": root.business_request_key,
            "initiator": {"name": tx.actor_id, "principal_id": tx.principal_id,
                          "kind": principals[tx.principal_id].kind if tx.principal_id in principals else None},
            "participants": sorted({t.actor_id for t in txs}),
            "verified_principals": sorted([{"name": p.name, "principal_id": p.id, "kind": p.kind}
                                           for p in principals.values()], key=lambda d: d["name"]),
            "created_at": tx.created_at, "finalized_at": tx.finalized_at, "final_state": tx.state,
            "local_outcome": local_outcome([e for e in effects if e.transaction_id == tx.id]) if tx.parent_id else None,
            "outcome_statement": self._statement(final, effects, open_residuals),
            "capability_summary": [{
                "subject_id": c.subject_id, "issuer": c.issuer, "parent_capability_id": c.parent_capability_id,
                "allowed_effect_types": c.scope["allowed_effect_types"], "allowed_resources": c.scope["allowed_resources"],
                "amount_limit": _amount(c.amount_limit), "cumulative_amount_limit": _amount(c.cumulative_limit),
                "delegation_depth": c.delegation_depth, "expires_at": iso(c.expires_at),
            } for c in caps],
            "children": [{"transaction_id": t.id, "actor_id": t.actor_id, "final_state": t.state,
                          "local_outcome": local_outcome([e for e in effects if e.transaction_id == t.id]),
                          "receipt_hash": child_receipts.get(t.id)} for t in txs if t.parent_id == tx.id],
            "effects": [{
                "effect_id": e.id, "transaction_id": e.transaction_id, "actor_id": e.actor_id, "slot": e.slot,
                "effect_type": e.contract_type, "operation_identity": e.operation_key,
                "client_operation_key": e.client_operation_key,
                "contract": {"id": e.contract_id, "version": e.contract_version, "hash": e.contract_hash},
                "final_state": e.state, "application": e.application, "postcondition": e.postcondition,
                "restoration": e.restoration, "reversibility_class": e.reversibility_class,
                "requested_amount": _amount(e.amount), "observed_amount": _amount(e.observed_amount),
                "max_exposure": _amount(e.max_exposure), "currency": e.currency,
                "payload_fingerprint": e.payload_fingerprint, "payload": redact(e.payload),
                "depends_on": e.depends_on, "provider_reference": e.provider_reference, "legacy": e.legacy,
            } for e in effects],
            "invariant_results": sorted([{
                "key": inv_rows[ev.invariant_id].key, "name": inv_rows[ev.invariant_id].name, "phase": ev.phase,
                "context": ev.context, "passed": ev.passed, "reason": ev.reason,
                "failure_action": inv_rows[ev.invariant_id].failure_action, "observed": redact(ev.observed_values),
            } for ev in latest.values() if ev.invariant_id in inv_rows], key=lambda r: (r["key"], r["phase"], r["context"])),
            "commit_decision": None if decision is None else {
                "eligible": decision.eligible, "evaluated_at": decision.evaluated_at,
                "blocking_reasons": decision.blocking_reasons,
                "checks": [{"code": c["code"], "subject": c.get("subject"), "passed": c["passed"]} for c in decision.checks],
            },
            "approvals": [{"digest": a.digest, "approver": a.approver_name, "approver_principal_id": a.approver_principal_id,
                           "role": a.role, "reason": a.reason, "at": a.created_at, "expires_at": a.expires_at,
                           "revoked": a.revoked_at is not None} for a in approvals],
            "reservations": [{"resource": r.resource_key, "mode": r.mode, "status": r.status, "reason": r.reason,
                              "effect_id": r.effect_id} for r in reservations],
            "budget_ledger": [{"scope": key, "kind": b.kind, "amount": _amount(b.amount), "currency": b.currency,
                               "effect_id": b.effect_id, "note": b.note, "at": b.created_at} for b, key in budget],
            "execution_results": [self._attempt(a, by_id) for a in attempts if a.kind == "EXECUTE"],
            "reconciliation_events": [self._attempt(a, by_id) for a in attempts if a.kind == "RECONCILE"],
            "compensation_results": [self._attempt(a, by_id) for a in attempts if a.kind == "COMPENSATE"],
            "final_observation_set": [self._obs(o, by_id) for o in sorted(final_obs, key=lambda o: str(o.effect_id))],
            "observation_references": [{"observation_id": o.id, "effect_id": o.effect_id, "purpose": o.purpose,
                                        "evidence_digest": o.evidence_digest, "authoritative": o.authoritative,
                                        "at": o.observed_at} for o in all_obs],
            "uncertainty": [{"operation_identity": e.operation_key, "application": e.application,
                             "restoration": e.restoration}
                            for e in effects if Application(e.application) == Application.UNKNOWN
                            or e.restoration == Restoration.UNKNOWN],
            "residual_obligations": [{
                "id": r.id, "operation_identity": by_id[r.effect_id].operation_key, "kind": r.kind,
                "description": r.description, "amount": _amount(r.amount), "bound": _amount(r.bound_amount),
                "currency": r.currency, "blocking": r.blocking, "disposition": r.disposition,
                "required_remediation": r.required_remediation, "resolution": r.resolution,
                "evidence_observation_id": r.evidence_observation_id,
            } for r in residuals],
            "human_actions": [{"operator_id": a.operator_id, "action": a.action, "note": a.note,
                               "effect_id": a.effect_id, "at": a.created_at,
                               "kind": "ATTESTATION (not provider-verified)" if a.action.startswith("ATTEST")
                               else "OPERATOR_ACTION"} for a in actions],
            "external_references": sorted([{"operation_identity": e.operation_key, "provider": self._provider(e),
                                            "reference": e.provider_reference}
                                           for e in effects if e.provider_reference],
                                          key=lambda r: r["operation_identity"]),
            "provenance": sorted({o.provenance for o in all_obs}) or ["NONE"],
            "event_count": int(event_count),
            "integrity_note": "receipt_hash is a SHA-256 integrity digest, not a digital signature",
        }
        return normalized(payload)

    def _provider(self, e) -> str | None:
        return self.registry.contract(e.contract_type).provider if self.registry.has(e.contract_type) else None

    @staticmethod
    def _attempt(a: OperationAttemptRow, by_id: dict) -> dict[str, Any]:
        resp = a.response or {}
        return {"operation_identity": by_id[a.effect_id].operation_key, "kind": a.kind, "attempt_no": a.attempt_no,
                "status": a.status, "authoritative": a.authoritative, "http_status": resp.get("http_status"),
                "retry_justification": (a.request or {}).get("retry_justification"),
                "error": (a.error or {}).get("message"), "started_at": a.started_at, "finished_at": a.finished_at}

    @staticmethod
    def _obs(o: ObservationRow, by_id: dict) -> dict[str, Any]:
        return {"observation_id": o.id, "operation_identity": by_id[o.effect_id].operation_key, "purpose": o.purpose,
                "source": o.source, "provenance": o.provenance, "consistency": o.consistency,
                "application": o.application, "postcondition": o.postcondition, "observed_amount": _amount(o.observed_amount),
                "provider_reference": o.provider_reference, "evidence_digest": o.evidence_digest, "at": o.observed_at}

    @staticmethod
    def _statement(final: TransactionState, effects, open_residuals) -> str:
        n = len(effects)
        count = lambda st: sum(1 for e in effects if e.state == st)  # noqa: E731
        applied = sum(1 for e in effects if e.application == Application.APPLIED)
        if final == TransactionState.COMMITTED_VERIFIED:
            return f"All {n - count('ABORTED')} required effects were executed and independently verified."
        if final == TransactionState.ABORTED:
            return (f"Aborted before any protected effect was dispatched; {n} proposed effect(s) released. "
                    "No external state was changed by this transaction.")
        if final == TransactionState.COMPENSATED:
            return (f"Did not complete. {applied} applied effect(s) were restored and the restoration verified; "
                    f"{count('ABORTED')} were never dispatched. External history (e.g. provider logs, retained notes) "
                    "may still record that the changes happened.")
        return (f"Ended {final} with {len(open_residuals)} open residual obligation(s); {applied} effect(s) applied, "
                f"{sum(1 for e in effects if e.restoration == Restoration.RESTORED)} restored. "
                "See residual_obligations for what remains in effect.")

    async def finalize(self, s: AsyncSession, rows: TreeRows, tx_id: UUID) -> ReceiptRow | None:
        tx = rows.txs[tx_id]
        if TransactionState(tx.state) not in TERMINAL_TX_STATES:
            return None
        existing = (await s.execute(select(ReceiptRow).where(ReceiptRow.transaction_id == tx_id))).scalar_one_or_none()
        if existing is not None:
            return existing  # immutable; finalization is idempotent
        with span("receipt.finalize", transaction_id=tx_id, root_id=tx.root_id, state=tx.state):
            payload = await self.build_payload(s, rows, tx_id)
            digest = receipt_digest(payload)
            payload["receipt_hash"] = digest
            row = ReceiptRow(transaction_id=tx_id, root_id=tx.root_id, receipt_version=RECEIPT_VERSION_V2,
                             final_state=tx.state, payload=payload, sha256=digest)
            s.add(row)
            await s.flush()
            append_event(s, tx, EventType.TRANSACTION_FINALIZED, {"final_state": tx.state})
            append_event(s, tx, EventType.RECEIPT_CREATED, {"receipt_hash": digest, "receipt_version": RECEIPT_VERSION_V2})
            return row

    async def amend(self, s: AsyncSession, receipt: ReceiptRow, reason: str, payload: dict[str, Any]) -> ReceiptAmendmentRow:
        prior = list((await s.execute(select(ReceiptAmendmentRow).where(ReceiptAmendmentRow.receipt_id == receipt.id)
                                      .order_by(ReceiptAmendmentRow.sequence_no))).scalars())
        previous = prior[-1].sha256 if prior else receipt.sha256
        body = normalized({"receipt_id": receipt.id, "transaction_id": receipt.transaction_id,
                           "sequence_no": len(prior) + 1, "reason": reason, "previous_hash": previous,
                           "payload": payload})
        digest = receipt_digest(body)
        row = ReceiptAmendmentRow(receipt_id=receipt.id, transaction_id=receipt.transaction_id,
                                  sequence_no=len(prior) + 1, reason=reason, payload=body, previous_hash=previous,
                                  sha256=digest)
        s.add(row)
        return row
