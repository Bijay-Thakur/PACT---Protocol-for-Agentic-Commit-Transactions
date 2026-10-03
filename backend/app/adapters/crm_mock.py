"""CRM adapter. Updates are REVERSIBLE by restoring the before-image captured at prepare."""

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


class CrmUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(min_length=1)
    lifecycle_state: str = Field(pattern=r"^[a-z_]{3,48}$")
    note: str | None = None


CONTRACT = EffectContract(
    effect_type="crm.update",
    adapter_name="crm_mock",
    resource_type="customer.crm_record",
    operation_kind="record_update",
    required_capability="crm.update",
    reversibility_class=ReversibilityClass.REVERSIBLE,
    prepare_strategy=PrepareStrategy.READ_PRECONDITIONS,
    verification_strategy=VerificationStrategy.QUERY_TARGET_STATE,
    compensation_strategy=CompensationStrategy.RESTORE_BEFORE_IMAGE,
    idempotency_strategy=IdempotencyStrategy.TARGET_STATE_IDEMPOTENT,
    reconciliation_policy=ReconciliationPolicy.QUERY_TARGET_STATE,
    evidence_requirements=["lifecycle_state"],
    description="Update the CRM lifecycle state (compensation: restore before-image).",
    payload_model=CrmUpdatePayload,
    required_claims=lambda p: [
        ResourceClaim(resource=f"customer:{p['customer_id']}/crm_record", mode=ClaimMode.WRITE)
    ],
)


class CrmUpdateAdapter(HttpAdapter):
    contract = CONTRACT

    def _path(self, effect: EffectView) -> str:
        return f"/sim/crm/accounts/{effect.payload['customer_id']}"

    async def prepare(self, effect: EffectView, ctx: AdapterContext) -> PrepareResult:
        status, body = await self._read(ctx, self._path(effect))
        if status != 200:
            return PrepareResult(ok=False, reason=f"crm account unreadable (status={status})")
        return PrepareResult(ok=True, observations={"before_image": {"lifecycle_state": body["lifecycle_state"]}})

    async def execute(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult:
        return await self._send(ctx, "PATCH", self._path(effect), headers=ctx.headers(effect), json={
            "lifecycle_state": effect.payload["lifecycle_state"], "note": effect.payload.get("note"),
        })

    async def _expect(self, effect: EffectView, ctx: AdapterContext, target: str) -> VerificationResult:
        status, body = await self._read(ctx, self._path(effect))
        if status != 200:
            return unreadable(f"crm account unreadable (status={status})")
        evidence = {"lifecycle_state": body["lifecycle_state"]}
        if body["lifecycle_state"] == target:
            return VerificationResult(status=VerificationStatus.VERIFIED_SUCCESS, evidence=evidence,
                                      external_reference=f"crm:{effect.payload['customer_id']}",
                                      reason=f"lifecycle_state is {target}")
        return VerificationResult(status=VerificationStatus.VERIFIED_FAILURE, evidence=evidence,
                                  reason=f"lifecycle_state is {body['lifecycle_state']}, expected {target}")

    async def verify(self, effect: EffectView, ctx: AdapterContext) -> VerificationResult:
        return await self._expect(effect, ctx, effect.payload["lifecycle_state"])

    async def reconcile(self, effect: EffectView, ctx: AdapterContext) -> ReconciliationFinding:
        return await self._target_state_finding(await self.verify(effect, ctx))

    async def compensate(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult:
        before = effect.prepare_evidence.get("before_image", {}).get("lifecycle_state")
        return await self._send(ctx, "PATCH", self._path(effect),
                                headers=ctx.headers(effect, effect.provider_idempotency_key + "_comp"),
                                json={"lifecycle_state": before, "note": "PACT compensation: restored before-image"})

    async def verify_compensation(self, effect: EffectView, ctx: AdapterContext) -> VerificationResult:
        return await self._expect(effect, ctx, effect.prepare_evidence.get("before_image", {}).get("lifecycle_state", ""))
