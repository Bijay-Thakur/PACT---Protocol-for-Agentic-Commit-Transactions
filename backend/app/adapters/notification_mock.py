"""Notification adapter. A sent message cannot be unsent: IRREVERSIBLE."""

from __future__ import annotations

from typing import Any, Literal

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

TEMPLATES = {"cancellation_confirmation", "refund_confirmation", "service_credit_notice"}


class NotificationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(min_length=1)
    template: str
    channel: Literal["email"] = "email"
    variables: dict[str, Any] = Field(default_factory=dict)


CONTRACT = EffectContract(
    effect_type="notification.send",
    adapter_name="notification_mock",
    resource_type="customer.notifications",
    operation_kind="communication",
    required_capability="notification.send",
    reversibility_class=ReversibilityClass.IRREVERSIBLE,
    prepare_strategy=PrepareStrategy.READ_PRECONDITIONS,
    verification_strategy=VerificationStrategy.QUERY_BY_IDEMPOTENCY_KEY,
    compensation_strategy=CompensationStrategy.NONE,
    idempotency_strategy=IdempotencyStrategy.PROVIDER_IDEMPOTENCY_KEY,
    reconciliation_policy=ReconciliationPolicy.QUERY_BY_IDEMPOTENCY_KEY,
    evidence_requirements=["message_id", "status"],
    description="Send a customer-facing message.",
    payload_model=NotificationPayload,
    required_claims=lambda p: [
        ResourceClaim(resource=f"customer:{p['customer_id']}/notifications", mode=ClaimMode.WRITE)
    ],
)


class NotificationAdapter(HttpAdapter):
    contract = CONTRACT

    async def prepare(self, effect: EffectView, ctx: AdapterContext) -> PrepareResult:
        template = effect.payload["template"]
        if template not in TEMPLATES:
            return PrepareResult(ok=False, reason=f"unknown template {template!r}")
        return PrepareResult(ok=True, observations={"template": template})

    async def execute(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult:
        p = effect.payload
        return await self._send(ctx, "POST", "/sim/notification/messages", headers=ctx.headers(effect), json={
            "customer_id": p["customer_id"], "template": p["template"],
            "channel": p.get("channel", "email"), "variables": p.get("variables", {}),
        })

    async def _lookup(self, effect: EffectView, ctx: AdapterContext) -> tuple[int | None, list[dict]]:
        status, body = await self._read(ctx, "/sim/notification/messages", params={
            "customer_id": effect.payload["customer_id"], "idempotency_key": effect.provider_idempotency_key,
        })
        return status, (body or {}).get("data", []) if status == 200 else []

    async def verify(self, effect: EffectView, ctx: AdapterContext) -> VerificationResult:
        status, msgs = await self._lookup(effect, ctx)
        if status != 200:
            return unreadable(f"message lookup failed (status={status})")
        if not msgs:
            return VerificationResult(status=VerificationStatus.VERIFICATION_PENDING,
                                      reason="message not visible yet", evidence={"match_count": 0})
        m = msgs[0]
        evidence = {"message_id": m["id"], "status": m["status"], "match_count": len(msgs)}
        if m["status"] == "delivered":
            return VerificationResult(status=VerificationStatus.VERIFIED_SUCCESS, external_reference=m["id"],
                                      evidence=evidence, reason="message delivered")
        return VerificationResult(status=VerificationStatus.VERIFICATION_PENDING, evidence=evidence,
                                  reason=f"message status {m['status']}")

    async def reconcile(self, effect: EffectView, ctx: AdapterContext) -> ReconciliationFinding:
        status, msgs = await self._lookup(effect, ctx)
        if status != 200:
            return ReconciliationFinding(finding=ProviderFinding.INDETERMINATE, reason="lookup unavailable")
        if msgs:
            return ReconciliationFinding(finding=ProviderFinding.APPLIED, external_reference=msgs[0]["id"],
                                         evidence={"message": msgs[0]}, reason="message exists for key")
        return ReconciliationFinding(finding=ProviderFinding.NOT_APPLIED, reason="no message for key")
