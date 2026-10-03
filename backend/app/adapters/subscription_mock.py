"""Subscription service adapter. Cancellation is COMPENSABLE (reactivate)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.adapters.base import AdapterContext, HttpAdapter, unreadable
from app.domain.effect import EffectContract, EffectView
from app.domain.enums import (
    ClaimMode,
    CompensationStrategy,
    IdempotencyStrategy,
    PrepareStrategy,
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


class CancelPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(min_length=1)
    reason: str = "customer requested cancellation"


CONTRACT = EffectContract(
    effect_type="subscription.cancel",
    adapter_name="subscription_mock",
    resource_type="customer.subscription",
    operation_kind="state_change",
    required_capability="subscription.cancel",
    reversibility_class=ReversibilityClass.COMPENSABLE,
    prepare_strategy=PrepareStrategy.READ_PRECONDITIONS,
    verification_strategy=VerificationStrategy.QUERY_TARGET_STATE,
    compensation_strategy=CompensationStrategy.INVERSE_OPERATION,
    idempotency_strategy=IdempotencyStrategy.TARGET_STATE_IDEMPOTENT,
    reconciliation_policy=ReconciliationPolicy.QUERY_TARGET_STATE,
    evidence_requirements=["status"],
    description="Cancel the customer's subscription (compensation: reactivate).",
    payload_model=CancelPayload,
    required_claims=lambda p: [
        ResourceClaim(resource=f"customer:{p['customer_id']}/subscription", mode=ClaimMode.WRITE)
    ],
)


class SubscriptionCancelAdapter(HttpAdapter):
    contract = CONTRACT

    def _path(self, effect: EffectView) -> str:
        return f"/sim/subscription/customers/{effect.payload['customer_id']}"

    async def _status(self, effect: EffectView, ctx: AdapterContext) -> tuple[int | None, dict]:
        status, body = await self._read(ctx, self._path(effect))
        return status, body if isinstance(body, dict) else {}

    async def prepare(self, effect: EffectView, ctx: AdapterContext) -> PrepareResult:
        status, body = await self._status(effect, ctx)
        if status != 200:
            return PrepareResult(ok=False, reason=f"subscription unreadable (status={status})")
        if body["status"] != "active":
            return PrepareResult(ok=False, reason=f"subscription is {body['status']}, expected active")
        return PrepareResult(ok=True, observations={
            "before_status": body["status"], "plan": body["plan"],
            "unused_balance": body["unused_balance"], "period_end": body["period_end"],
        })

    async def execute(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult:
        return await self._send(ctx, "POST", f"{self._path(effect)}/cancel", headers=ctx.headers(effect))

    async def _expect(self, effect: EffectView, ctx: AdapterContext, target: str) -> VerificationResult:
        status, body = await self._status(effect, ctx)
        if status != 200:
            return unreadable(f"subscription unreadable (status={status})")
        evidence = {"status": body["status"], "plan": body["plan"]}
        if body["status"] == target:
            return VerificationResult(status=VerificationStatus.VERIFIED_SUCCESS, evidence=evidence,
                                      external_reference=f"sub:{effect.payload['customer_id']}",
                                      reason=f"subscription is {target}")
        return VerificationResult(status=VerificationStatus.VERIFIED_FAILURE, evidence=evidence,
                                  reason=f"subscription is {body['status']}, expected {target}")

    async def verify(self, effect: EffectView, ctx: AdapterContext) -> VerificationResult:
        return await self._expect(effect, ctx, "cancelled")

    async def reconcile(self, effect: EffectView, ctx: AdapterContext) -> ReconciliationFinding:
        return await self._target_state_finding(await self.verify(effect, ctx))

    async def compensate(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult:
        return await self._send(ctx, "POST", f"{self._path(effect)}/reactivate",
                                headers=ctx.headers(effect, effect.provider_idempotency_key + "_comp"))

    async def verify_compensation(self, effect: EffectView, ctx: AdapterContext) -> VerificationResult:
        return await self._expect(effect, ctx, effect.prepare_evidence.get("before_status", "active"))
