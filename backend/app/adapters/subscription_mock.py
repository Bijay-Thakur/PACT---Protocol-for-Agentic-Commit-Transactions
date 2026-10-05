"""Subscription adapter (contract subscription.cancel v2.0.0). COMPENSABLE.

- Cancel is conditional on the version observed at prepare (``If-Match``): if
  the subscription changed since the plan was frozen, the provider answers 412
  and nothing is applied.
- Observation distinguishes "the target state holds" (postcondition) from "this
  operation caused it" (application, evidenced by provider history keyed by the
  idempotency key).
- Restoration re-applies the *before-image* (status + plan) only if the record
  still carries the version PACT produced; otherwise a later legitimate change
  exists and restoration is refused as stale (residual).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.adapters.base import AdapterContext, HttpAdapter, unreadable
from app.domain.effect import EffectContract, EffectView
from app.domain.enums import (
    Application,
    ClaimMode,
    CompensationStrategy,
    IdempotencyStrategy,
    Postcondition,
    PrepareStrategy,
    ReconciliationPolicy,
    Restoration,
    ReversibilityClass,
    VerificationStrategy,
)
from app.domain.resource_claim import ResourceClaim
from app.domain.verification import DispatchResult, Observation, PrepareResult


class CancelPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="customer requested cancellation", max_length=500)


CONTRACT = EffectContract(
    effect_type="subscription.cancel",
    adapter_name="subscription_mock",
    provider="subscription-sim",
    resource_type="customer.subscription",
    operation_kind="state_change",
    required_capability="subscription.cancel",
    reversibility_class=ReversibilityClass.COMPENSABLE,
    prepare_strategy=PrepareStrategy.READ_PRECONDITIONS,
    verification_strategy=VerificationStrategy.QUERY_TARGET_STATE,
    compensation_strategy=CompensationStrategy.INVERSE_OPERATION,
    idempotency_strategy=IdempotencyStrategy.TARGET_STATE_IDEMPOTENT,
    reconciliation_policy=ReconciliationPolicy.QUERY_TARGET_STATE,
    evidence_requirements=["status", "version", "history"],
    negative_evidence="AFTER_INFLIGHT_WINDOW",
    max_inflight_s=2.0,
    idempotency_window_s=None,
    # Re-sending the same cancellation with the same key is a provider-side no-op.
    safe_retry_statuses={503: "TARGET_STATE_IDEMPOTENT: the provider records the key; a replay cannot cancel twice"},
    restoration_scope="status and plan restored to the before-image if no later change exists",
    description="Cancel the customer's subscription (restoration: reactivate before-image, version-guarded).",
    payload_model=CancelPayload,
    required_claims=lambda p: [
        ResourceClaim(resource=f"subscription/customer:{p['customer_id']}/subscription", mode=ClaimMode.WRITE)
    ],
)


class SubscriptionCancelAdapter(HttpAdapter):
    contract = CONTRACT

    def _path(self, effect: EffectView) -> str:
        return f"/sim/subscription/customers/{effect.payload['customer_id']}"

    async def _get(self, effect: EffectView, ctx: AdapterContext) -> tuple[int | None, dict]:
        status, body = await self._read(ctx, self._path(effect))
        return status, body if isinstance(body, dict) else {}

    async def prepare(self, effect: EffectView, ctx: AdapterContext) -> PrepareResult:
        status, body = await self._get(effect, ctx)
        if status != 200:
            return PrepareResult(ok=False, reason=f"subscription unreadable (status={status})")
        if body["status"] != "active":
            return PrepareResult(ok=False, reason=f"subscription is {body['status']}, expected active")
        return PrepareResult(
            ok=True,
            observations={"before_status": body["status"], "before_plan": body["plan"],
                          "unused_balance": body["unused_balance"], "period_start": body["period_start"],
                          "period_end": body["period_end"], "version": body["version"]},
            preconditions={"status": body["status"], "plan": body["plan"], "version": body["version"]},
        )

    async def execute(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult:
        version = (effect.prepare_evidence or {}).get("preconditions", {}).get("version")
        return await self._send(ctx, "POST", f"{self._path(effect)}/cancel",
                                headers=ctx.headers(effect, if_match=version))

    async def observe(self, effect: EffectView, ctx: AdapterContext) -> Observation:
        src = "subscription-sim:/subscription/customers"
        status, body = await self._get(effect, ctx)
        if status != 200:
            return unreadable(f"subscription unreadable (status={status})", source=src)
        mine = [h for h in body.get("history", []) if h.get("key") == effect.provider_idempotency_key
                and h.get("op") == "cancel"]
        evidence = {"status": body["status"], "plan": body["plan"], "version": body["version"],
                    "history_entries_for_operation": mine}
        if mine and mine[-1].get("changed"):
            application = Application.APPLIED
        elif mine:
            application = Application.NOT_APPLIED_CONFIRMED  # provider recorded a no-op
        elif body["status"] == "cancelled":
            application = Application.UNKNOWN  # target holds, but nothing proves this operation caused it
        else:
            application = Application.NOT_APPLIED_CONFIRMED  # strongly consistent read: not cancelled, no record
        post = Postcondition.MATCH if body["status"] == "cancelled" else Postcondition.MISMATCH
        return Observation(application=application, postcondition=post, evidence=evidence, source=src,
                           provider_reference=f"sub:{effect.payload['customer_id']}@v{body['version']}",
                           reason=f"subscription is {body['status']} (v{body['version']})")

    async def restore(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult:
        produced = _produced_version(effect)
        before_plan = (effect.prepare_evidence or {}).get("observations", {}).get("before_plan")
        return await self._send(ctx, "POST", f"{self._path(effect)}/reactivate", json={"plan": before_plan},
                                headers=ctx.headers(effect, effect.provider_idempotency_key + "_restore",
                                                    if_match=produced))

    async def observe_restoration(self, effect: EffectView, ctx: AdapterContext) -> Observation:
        src = "subscription-sim:/subscription/customers"
        status, body = await self._get(effect, ctx)
        if status != 200:
            return unreadable(f"subscription unreadable (status={status})", source=src)
        obs = (effect.prepare_evidence or {}).get("observations", {})
        restored = body["status"] == obs.get("before_status", "active") and body["plan"] == obs.get("before_plan")
        return Observation(application=Application.APPLIED, postcondition=Postcondition.MATCH,
                           restoration=Restoration.RESTORED if restored else Restoration.RESIDUAL,
                           evidence={"status": body["status"], "plan": body["plan"], "version": body["version"]},
                           source=src, reason="before-image restored" if restored else "before-image not restored")


def _produced_version(effect: EffectView) -> int | None:
    """Version PACT's own cancellation produced (from the verification observation)."""
    ev = (effect.verification_result or {}).get("evidence", {})
    entries = ev.get("history_entries_for_operation") or []
    return entries[-1].get("version") if entries else None
