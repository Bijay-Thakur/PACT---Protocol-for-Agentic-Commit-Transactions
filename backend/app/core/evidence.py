"""Evidence ledger: observations, residual obligations, budget accounting, retry justification.

Single place where external evidence changes what PACT believes:

- ``record_observation`` persists normalized evidence (append-only) with a digest.
- ``normalize_negative`` applies the contract's negative-evidence rule: an empty
  or "not applied" read is only authoritative when the provider's guarantees say
  so (strong read after the in-flight window, or eventual read after in-flight +
  consistency lag). Deadlines never manufacture authoritative absence.
- ``apply_outcome`` sets the effect's application/postcondition/observed amount,
  books budget consumption for *observed* amounts, keeps conservative holds for
  unknown outcomes, and opens residual obligations for applied mismatches.
- ``retry_justification`` returns the exact contract-grounded reason a re-dispatch
  is safe, or None (then the effect must reconcile or escalate instead).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.reservations import BudgetService
from app.domain.effect import EffectContract
from app.domain.enums import (
    Application,
    AttemptKind,
    BudgetEntryKind,
    Consistency,
    EventType,
    ObservationPurpose,
    Postcondition,
    ResidualDisposition,
    ResidualKind,
)
from app.domain.verification import Observation
from app.persistence.models import EffectRow, ObservationRow, OperationAttemptRow, ResidualObligationRow, TransactionRow
from app.persistence.repositories import append_event

AMBIGUOUS_ATTEMPT_STATUSES = {"RESPONSE_LOST", "INTENT_RECORDED", "ABANDONED_BY_RESTART", "STALE_LATE_RESPONSE"}


def _now() -> datetime:
    return datetime.now(UTC)


def evidence_digest(evidence: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


async def execute_attempts(s: AsyncSession, effect_id: UUID, kind: AttemptKind = AttemptKind.EXECUTE) -> list[OperationAttemptRow]:
    return list((await s.execute(select(OperationAttemptRow).where(
        OperationAttemptRow.effect_id == effect_id, OperationAttemptRow.kind == str(kind))
        .order_by(OperationAttemptRow.started_at, OperationAttemptRow.attempt_no))).scalars())


def last_dispatch(attempts: list[OperationAttemptRow]) -> tuple[bool, datetime | None]:
    """(was the last authoritative dispatch ambiguous?, when was it sent)."""
    auth = [a for a in attempts if a.authoritative]
    if not auth:
        return False, None
    last = auth[-1]
    return last.status in AMBIGUOUS_ATTEMPT_STATUSES, last.started_at


def normalize_negative(contract: EffectContract, obs: Observation, *, ambiguous: bool,
                       sent_at: datetime | None, now: datetime | None = None) -> tuple[Observation, bool]:
    """Return (observation, negative_authoritative) after applying the contract's rule."""
    now = now or _now()
    negative = obs.absent or obs.application == Application.NOT_APPLIED_CONFIRMED
    if not negative or not obs.readable:
        return obs, False
    if contract.negative_evidence == "NONE":
        authoritative = False
    elif not ambiguous:
        # The provider answered the dispatch; nothing can still land later.
        authoritative = obs.consistency == Consistency.STRONG or not obs.absent
        if obs.absent and obs.consistency == Consistency.EVENTUAL and sent_at is not None:
            authoritative = (now - sent_at).total_seconds() >= contract.consistency_lag_s
    else:
        window = contract.max_inflight_s + (contract.consistency_lag_s if obs.consistency == Consistency.EVENTUAL else 0)
        authoritative = sent_at is not None and (now - sent_at).total_seconds() >= window
    if authoritative:
        return obs.model_copy(update={"application": Application.NOT_APPLIED_CONFIRMED, "pending": False}), True
    return obs.model_copy(update={"application": Application.UNKNOWN,
                                  "reason": obs.reason + " (negative evidence not yet authoritative)"}), False


def retry_justification(contract: EffectContract, attempts: list[OperationAttemptRow], *,
                        negative_authoritative: bool, now: datetime | None = None) -> str | None:
    now = now or _now()
    if negative_authoritative:
        return ("AUTHORITATIVE_NEGATIVE_EVIDENCE: provider read after the in-flight window shows the operation "
                "was not applied")
    first = next((a for a in attempts if a.authoritative), None)
    if contract.idempotency_window_s and first is not None:
        age = (now - first.started_at).total_seconds()
        if age < contract.idempotency_window_s:
            return (f"PROVIDER_IDEMPOTENCY_DEDUP: replay with the same key {first.request.get('idempotency_key')} "
                    f"{age:.1f}s after the first attempt, inside the declared {contract.idempotency_window_s:.0f}s "
                    "dedup window; the provider cannot apply it twice")
    return None


class EvidenceLedger:
    def __init__(self, budgets: BudgetService, provenance: str = "SIMULATED"):
        self.budgets = budgets
        self.provenance = provenance

    def record_observation(self, s: AsyncSession, eff: EffectRow, obs: Observation, purpose: ObservationPurpose, *,
                           attempt_id: UUID | None = None, authoritative: bool = True,
                           negative_authoritative: bool = False) -> ObservationRow:
        row = ObservationRow(
            tenant_id=eff.tenant_id, root_id=eff.root_id, effect_id=eff.id, attempt_id=attempt_id,
            purpose=str(purpose), source=obs.source or "unknown", provenance=obs.provenance or self.provenance,
            provider_reference=obs.provider_reference, consistency=str(obs.consistency),
            application=str(obs.application), postcondition=str(obs.postcondition),
            restoration=str(obs.restoration) if obs.restoration else None,
            negative_authoritative=negative_authoritative, authoritative=authoritative,
            observed_amount=obs.observed_amount, evidence=obs.evidence, evidence_digest=evidence_digest(obs.evidence),
            reason=obs.reason or "",
        )
        s.add(row)
        return row

    def open_residual(self, s: AsyncSession, tx: TransactionRow, eff: EffectRow, kind: ResidualKind, description: str,
                      remediation: str, *, amount: Decimal | None = None, bound: Decimal | None = None,
                      currency: str | None = None, blocking: bool = True, observation_id: UUID | None = None,
                      disposition: ResidualDisposition = ResidualDisposition.OPEN) -> ResidualObligationRow:
        row = ResidualObligationRow(
            tenant_id=eff.tenant_id, root_id=eff.root_id, effect_id=eff.id, kind=str(kind), description=description,
            amount=amount, bound_amount=bound, currency=currency, required_remediation=remediation, blocking=blocking,
            disposition=str(disposition), evidence_observation_id=observation_id,
        )
        s.add(row)
        append_event(s, tx, EventType.RESIDUAL_RECORDED, {
            "kind": str(kind), "operation_key": eff.operation_key, "description": description,
            "amount": str(amount) if amount is not None else None, "bound": str(bound) if bound is not None else None,
            "currency": currency, "blocking": blocking, "disposition": str(disposition),
            "required_remediation": remediation}, effect_id=eff.id)
        return row

    async def open_residuals(self, s: AsyncSession, root_id: UUID, *, blocking_only: bool = True) -> list[ResidualObligationRow]:
        stmt = select(ResidualObligationRow).where(ResidualObligationRow.root_id == root_id,
                                                   ResidualObligationRow.disposition == ResidualDisposition.OPEN)
        if blocking_only:
            stmt = stmt.where(ResidualObligationRow.blocking.is_(True))
        return list((await s.execute(stmt)).scalars())

    async def apply_outcome(self, s: AsyncSession, tx: TransactionRow, eff: EffectRow, obs: Observation,
                            observation: ObservationRow | None) -> None:
        """Update outcome dimensions, book budget effects, open residuals for applied mismatches."""
        eff.application = str(obs.application)
        eff.postcondition = str(obs.postcondition)
        if obs.observed_amount is not None:
            eff.observed_amount = obs.observed_amount
        if obs.provider_reference:
            eff.provider_reference = obs.provider_reference
        await self._book_budget(s, tx, eff, obs)
        if obs.application == Application.APPLIED and obs.postcondition in (Postcondition.MISMATCH, Postcondition.PARTIAL):
            existing = (await s.execute(select(ResidualObligationRow).where(
                ResidualObligationRow.effect_id == eff.id, ResidualObligationRow.kind == ResidualKind.APPLIED_MISMATCH,
                ResidualObligationRow.disposition == ResidualDisposition.OPEN))).first()
            if existing is None:
                self.open_residual(
                    s, tx, eff, ResidualKind.APPLIED_MISMATCH,
                    f"{eff.contract_type} was applied but does not match the approved plan: {obs.reason}",
                    "Review the provider record; restore if the contract allows, otherwise remediate manually.",
                    amount=obs.observed_amount, currency=eff.currency,
                    observation_id=observation.id if observation is not None else None)

    async def _book_budget(self, s: AsyncSession, tx: TransactionRow, eff: EffectRow, obs: Observation) -> None:
        scopes = await self.budgets.scopes_for_effect(s, eff.root_id, eff.id)
        if not scopes:
            return
        observed_currency = None
        if obs.evidence.get("refunds"):
            currencies = {r.get("currency") for r in obs.evidence["refunds"]}
            observed_currency = currencies.pop() if len(currencies) == 1 else "MIXED"
        for scope in scopes:
            held = await self.budgets.effect_hold(s, scope.id, eff.id)
            if obs.application == Application.APPLIED:
                if observed_currency not in (None, scope.currency):
                    # Cannot account foreign-currency money against this scope: keep the hold as liability.
                    append_event(s, tx, EventType.BUDGET_HELD, {"scope": scope.key, "note": "currency mismatch; hold "
                                 f"retained ({observed_currency} observed)"}, effect_id=eff.id)
                    continue
                observed = obs.observed_amount if obs.observed_amount is not None else eff.amount
                already = await self.budgets.effect_consumed(s, scope.id, eff.id)
                consumed = (observed or Decimal("0")) - already  # idempotent: book only the observed delta
                self.budgets.entry(s, scope, eff.root_id, eff.id, BudgetEntryKind.CONSUME, consumed,
                                   f"observed application ({obs.postcondition})")
                if consumed <= 0 and held <= 0:
                    continue
                self.budgets.entry(s, scope, eff.root_id, eff.id, BudgetEntryKind.RELEASE_HOLD, held,
                                   "hold replaced by observed consumption")
                append_event(s, tx, EventType.BUDGET_CONSUMED, {"scope": scope.key, "consumed": str(consumed),
                                                                "released_hold": str(held)}, effect_id=eff.id)
            elif obs.application == Application.NOT_APPLIED_CONFIRMED:
                self.budgets.entry(s, scope, eff.root_id, eff.id, BudgetEntryKind.RELEASE_HOLD, held,
                                   "authoritative non-application")
            elif obs.application == Application.UNKNOWN:
                bound = max(eff.max_exposure or Decimal("0"), eff.amount or Decimal("0"))
                if bound > held:
                    self.budgets.entry(s, scope, eff.root_id, eff.id, BudgetEntryKind.HOLD, bound - held,
                                       "conservative bound for an unknown outcome")
                    append_event(s, tx, EventType.BUDGET_HELD, {"scope": scope.key, "bound": str(bound),
                                                                "note": "unknown outcome: conservative hold"},
                                 effect_id=eff.id)

    async def release_unsent(self, s: AsyncSession, eff: EffectRow, note: str) -> None:
        for scope in await self.budgets.scopes_for_effect(s, eff.root_id, eff.id):
            held = await self.budgets.effect_hold(s, scope.id, eff.id)
            self.budgets.entry(s, scope, eff.root_id, eff.id, BudgetEntryKind.RELEASE_HOLD, held, note)
