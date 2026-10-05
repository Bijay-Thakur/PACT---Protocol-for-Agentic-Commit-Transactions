"""Billing / Stripe-like refund adapter (contract billing.refund v2.0.0).

Refunds move money and cannot be undone by PACT: IRREVERSIBLE, no compensation.

Guarantees declared by this contract (and relied upon by the core):
- Idempotency: the provider deduplicates by ``Idempotency-Key`` for
  ``idempotency_window_s``. A replay inside that window cannot create a second
  refund; that is the *only* justification for retrying after an ambiguous call
  without authoritative negative evidence.
- Observation: refunds are searchable by PACT operation identity (metadata),
  independent of idempotency retention. Reads are EVENTUALLY consistent; an empty
  result is authoritative only after ``max_inflight_s + consistency_lag_s``
  since the last dispatch.
- 503/500/timeouts are NOT proof of non-application (apply-then-503 happens).
- Verification checks customer, charge, currency, amount, status and cardinality.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.adapters.base import AdapterContext, HttpAdapter, unreadable
from app.domain.effect import EffectContract, EffectView
from app.domain.enums import (
    Application,
    ClaimMode,
    CompensationStrategy,
    Consistency,
    IdempotencyStrategy,
    Postcondition,
    PrepareStrategy,
    ReconciliationPolicy,
    ReversibilityClass,
    VerificationStrategy,
)
from app.domain.resource_claim import ResourceClaim
from app.domain.verification import DispatchResult, Observation, PrepareResult


class RefundPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(min_length=1, max_length=128)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2, allow_inf_nan=False)
    currency: Literal["USD"] = "USD"
    charge_id: str | None = Field(default=None, max_length=128)
    reason: str = Field(default="unused-period refund", max_length=500)


def refund_resource(payload: dict) -> str:
    base = f"billing/customer:{payload['customer_id']}/refunds"
    return f"{base}/charge:{payload['charge_id']}" if payload.get("charge_id") else base


CONTRACT = EffectContract(
    effect_type="billing.refund",
    adapter_name="billing_mock",
    provider="billing-sim",
    resource_type="customer.refund_balance",
    operation_kind="financial_debit",
    required_capability="billing.refund",
    reversibility_class=ReversibilityClass.IRREVERSIBLE,
    prepare_strategy=PrepareStrategy.READ_PRECONDITIONS,
    verification_strategy=VerificationStrategy.QUERY_BY_IDEMPOTENCY_KEY,
    compensation_strategy=CompensationStrategy.NONE,
    idempotency_strategy=IdempotencyStrategy.PROVIDER_IDEMPOTENCY_KEY,
    reconciliation_policy=ReconciliationPolicy.QUERY_BY_IDEMPOTENCY_KEY,
    evidence_requirements=["refund_id", "customer_id", "charge_id", "currency", "amount", "status", "match_count"],
    amount_field="amount",
    supported_currencies=["USD"],
    consistency=Consistency.EVENTUAL,
    consistency_lag_s=1.0,
    max_inflight_s=2.0,
    negative_evidence="AFTER_INFLIGHT_WINDOW",
    idempotency_window_s=86400.0,
    definitive_rejection_statuses=[400, 404, 409, 422],
    safe_retry_statuses={},
    restoration_scope="none: refunded money cannot be recalled by PACT",
    description="Refund money to the customer's original payment method on a resolved charge.",
    payload_model=RefundPayload,
    required_claims=lambda p: [ResourceClaim(resource=refund_resource(p), mode=ClaimMode.EXCLUSIVE)],
)


class BillingRefundAdapter(HttpAdapter):
    contract = CONTRACT

    async def prepare(self, effect: EffectView, ctx: AdapterContext) -> PrepareResult:
        cid = effect.payload["customer_id"]
        status, body = await self._read(ctx, f"/sim/billing/customers/{cid}")
        if status != 200:
            return PrepareResult(ok=False, reason=f"billing account unreadable (status={status})")
        currency = effect.payload.get("currency", "USD")
        if body["currency"] != currency:
            return PrepareResult(ok=False, reason=f"refund currency {currency} != account currency {body['currency']}")
        amount = Decimal(str(effect.payload["amount"]))
        charge_id = effect.payload.get("charge_id")
        for ch in sorted(body["charges"], key=lambda c: c["id"]):
            refundable = Decimal(ch["amount"]) - Decimal(ch["refunded"])
            if (charge_id is None or ch["id"] == charge_id) and refundable >= amount:
                return PrepareResult(
                    ok=True,
                    observations={"charge_id": ch["id"], "refundable": str(refundable), "currency": body["currency"]},
                    resolved={"charge_id": ch["id"]},  # execution is pinned to this exact charge
                    preconditions={"charge_id": ch["id"], "charge_refunded": ch["refunded"],
                                   "account_currency": body["currency"]},
                    max_exposure=refundable,  # the most this charge could still refund
                )
        return PrepareResult(ok=False, reason="no charge with sufficient refundable balance",
                             observations={"requested": str(amount)})

    async def execute(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult:
        p = effect.payload
        return await self._send(ctx, "POST", "/sim/billing/refunds", headers=ctx.headers(effect), json={
            "customer_id": p["customer_id"], "amount": str(p["amount"]), "currency": p.get("currency", "USD"),
            "charge_id": p["charge_id"], "reason": p.get("reason", ""),
            "metadata": {"pact_operation": effect.operation_key, "pact_effect_id": str(effect.id)},
        })

    async def observe(self, effect: EffectView, ctx: AdapterContext) -> Observation:
        p = effect.payload
        status, body = await self._read(ctx, "/sim/billing/refunds", params={
            "customer_id": p["customer_id"], "pact_operation": effect.operation_key})
        src = "billing-sim:/billing/refunds?pact_operation"
        if status != 200:
            return unreadable(f"refund lookup failed (status={status})", source=src)
        refunds = body.get("data", [])
        if not refunds:
            return Observation(application=Application.UNKNOWN, postcondition=Postcondition.UNDETERMINED,
                               consistency=Consistency.EVENTUAL, absent=True, pending=True, source=src,
                               evidence={"match_count": 0}, reason="no refund visible for this operation")
        expected = {"customer_id": p["customer_id"], "charge_id": p.get("charge_id"),
                    "currency": p.get("currency", "USD"), "amount": str(Decimal(str(p["amount"])).quantize(Decimal("0.01"))),
                    "status": "succeeded"}
        total = sum(Decimal(r["amount"]) for r in refunds)
        mismatches = []
        for r in refunds:
            for k, v in expected.items():
                if v is not None and str(r.get(k)) != str(v):
                    mismatches.append({"refund_id": r["id"], "field": k, "expected": v, "observed": r.get(k)})
        evidence = {"match_count": len(refunds), "refunds": [{k: r.get(k) for k in
                    ("id", "customer_id", "charge_id", "currency", "amount", "status")} for r in refunds],
                    "expected": expected, "mismatches": mismatches}
        if len(refunds) > 1:
            post, reason = Postcondition.MISMATCH, f"{len(refunds)} refunds for one logical operation (duplicate)"
        elif mismatches:
            post = Postcondition.MISMATCH
            reason = "refund applied but does not match: " + ", ".join(
                f"{m['field']} {m['observed']} != {m['expected']}" for m in mismatches)
        else:
            post, reason = Postcondition.MATCH, "exactly one refund matching customer, charge, currency and amount"
        return Observation(application=Application.APPLIED, postcondition=post, consistency=Consistency.EVENTUAL,
                           observed_amount=total, provider_reference=refunds[0]["id"], evidence=evidence,
                           reason=reason, source=src)
