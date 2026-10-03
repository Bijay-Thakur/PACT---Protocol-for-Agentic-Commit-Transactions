"""Billing / Stripe-like refund adapter.

Refunds move money and cannot be undone by PACT, so the contract declares them
IRREVERSIBLE with no compensation. The provider supports idempotency keys and a
lookup by key, which is what makes UNKNOWN reconciliation authoritative.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.adapters.base import AdapterContext, HttpAdapter, unreadable
from app.domain.effect import EffectContract, EffectView
from app.domain.enums import (
    ClaimMode,
    CompensationStrategy,
    IdempotencyStrategy,
    PrepareStrategy,
    ProviderFinding,
    ReconciliationPolicy,
    ReversibilityClass,
    VerificationStatus,
    VerificationStrategy,
)
from app.domain.resource_claim import ResourceClaim
from app.domain.verification import (
    DispatchResult,
    PrepareResult,
    ReconciliationFinding,
    VerificationResult,
)


class RefundPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(min_length=1)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: Literal["USD"] = "USD"
    charge_id: str | None = None
    reason: str = "unused-period refund"


def refund_resource(payload: dict) -> str:
    base = f"customer:{payload['customer_id']}/refund_balance"
    return f"{base}/{payload['charge_id']}" if payload.get("charge_id") else base


CONTRACT = EffectContract(
    effect_type="billing.refund",
    adapter_name="billing_mock",
    resource_type="customer.refund_balance",
    operation_kind="financial_debit",
    required_capability="billing.refund",
    reversibility_class=ReversibilityClass.IRREVERSIBLE,
    prepare_strategy=PrepareStrategy.READ_PRECONDITIONS,
    verification_strategy=VerificationStrategy.QUERY_BY_IDEMPOTENCY_KEY,
    compensation_strategy=CompensationStrategy.NONE,
    idempotency_strategy=IdempotencyStrategy.PROVIDER_IDEMPOTENCY_KEY,
    reconciliation_policy=ReconciliationPolicy.QUERY_BY_IDEMPOTENCY_KEY,
    evidence_requirements=["refund_id", "amount", "customer_id", "idempotency_key", "match_count"],
    amount_field="amount",
    description="Refund money to the customer's original payment method.",
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
        amount = Decimal(str(effect.payload["amount"]))
        charge_id = effect.payload.get("charge_id")
        for ch in body["charges"]:
            refundable = Decimal(ch["amount"]) - Decimal(ch["refunded"])
            if (charge_id is None or ch["id"] == charge_id) and refundable >= amount:
                return PrepareResult(ok=True, observations={
                    "charge_id": ch["id"], "refundable": str(refundable), "currency": body["currency"],
                })
        return PrepareResult(ok=False, reason="no charge with sufficient refundable balance",
                             observations={"requested": str(amount)})

    async def execute(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult:
        p = effect.payload
        return await self._send(ctx, "POST", "/sim/billing/refunds", headers=ctx.headers(effect), json={
            "customer_id": p["customer_id"], "amount": str(p["amount"]), "currency": p.get("currency", "USD"),
            "charge_id": p.get("charge_id"), "reason": p.get("reason", ""),
            "metadata": {"pact_operation_key": effect.operation_key, "pact_effect_id": str(effect.id)},
        })

    async def _lookup(self, effect: EffectView, ctx: AdapterContext) -> tuple[int | None, list[dict]]:
        status, body = await self._read(ctx, "/sim/billing/refunds", params={
            "customer_id": effect.payload["customer_id"],
            "idempotency_key": effect.provider_idempotency_key,
        })
        return status, (body or {}).get("data", []) if status == 200 else []

    async def verify(self, effect: EffectView, ctx: AdapterContext) -> VerificationResult:
        status, refunds = await self._lookup(effect, ctx)
        if status != 200:
            return unreadable(f"refund lookup failed (status={status})")
        if not refunds:
            return VerificationResult(status=VerificationStatus.VERIFICATION_PENDING,
                                      reason="no refund visible for idempotency key yet",
                                      evidence={"match_count": 0})
        if len(refunds) > 1:
            return VerificationResult(status=VerificationStatus.VERIFIED_FAILURE,
                                      reason="multiple refunds for one logical operation",
                                      evidence={"match_count": len(refunds)})
        rf = refunds[0]
        evidence = {"refund_id": rf["id"], "amount": rf["amount"], "customer_id": rf["customer_id"],
                    "idempotency_key": rf["idempotency_key"], "status": rf["status"], "match_count": 1}
        expected = Decimal(str(effect.payload["amount"]))
        if rf["status"] != "succeeded" or Decimal(rf["amount"]) != expected or rf["customer_id"] != effect.payload["customer_id"]:
            return VerificationResult(status=VerificationStatus.VERIFIED_FAILURE, evidence=evidence,
                                      reason="refund exists but does not match the proposed effect")
        return VerificationResult(status=VerificationStatus.VERIFIED_SUCCESS, external_reference=rf["id"],
                                  evidence=evidence, reason="refund found by idempotency key")

    async def reconcile(self, effect: EffectView, ctx: AdapterContext) -> ReconciliationFinding:
        status, refunds = await self._lookup(effect, ctx)
        if status != 200:
            return ReconciliationFinding(finding=ProviderFinding.INDETERMINATE,
                                         reason=f"billing lookup unavailable (status={status})")
        if len(refunds) == 1:
            return ReconciliationFinding(finding=ProviderFinding.APPLIED, external_reference=refunds[0]["id"],
                                         evidence={"refund": refunds[0], "match_count": 1},
                                         reason="refund exists for idempotency key")
        if not refunds:
            return ReconciliationFinding(finding=ProviderFinding.NOT_APPLIED, evidence={"match_count": 0},
                                         reason="provider has no refund for idempotency key")
        return ReconciliationFinding(finding=ProviderFinding.CONFLICTING, evidence={"match_count": len(refunds)},
                                     reason="more than one refund for one logical operation")
