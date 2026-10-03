"""Identity / entitlement adapter. Revoke and grant are mutual inverses."""

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


class EntitlementPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(min_length=1)
    entitlement: str = Field(default="premium", pattern=r"^[a-z0-9_\-]{2,32}$")


def _claims(p: dict) -> list[ResourceClaim]:
    return [ResourceClaim(resource=f"customer:{p['customer_id']}/entitlement", mode=ClaimMode.WRITE)]


def _contract(op: str, inverse: str) -> EffectContract:
    return EffectContract(
        effect_type=f"identity.{op}",
        adapter_name="identity_mock",
        resource_type="customer.entitlement",
        operation_kind="access_change",
        required_capability=f"identity.{op}",
        reversibility_class=ReversibilityClass.COMPENSABLE,
        prepare_strategy=PrepareStrategy.READ_PRECONDITIONS,
        verification_strategy=VerificationStrategy.QUERY_TARGET_STATE,
        compensation_strategy=CompensationStrategy.INVERSE_OPERATION,
        idempotency_strategy=IdempotencyStrategy.TARGET_STATE_IDEMPOTENT,
        reconciliation_policy=ReconciliationPolicy.QUERY_TARGET_STATE,
        evidence_requirements=["entitlements"],
        contradicts=[f"identity.{inverse}"],
        description=f"{op.capitalize()} a customer entitlement (compensation: {inverse}).",
        payload_model=EntitlementPayload,
        required_claims=_claims,
    )


REVOKE_CONTRACT = _contract("revoke", "grant")
GRANT_CONTRACT = _contract("grant", "revoke")


class EntitlementAdapter(HttpAdapter):
    def __init__(self, op: str):
        self.op = op
        self.inverse = "grant" if op == "revoke" else "revoke"
        self.contract = REVOKE_CONTRACT if op == "revoke" else GRANT_CONTRACT

    def _base(self, effect: EffectView) -> str:
        return f"/sim/identity/customers/{effect.payload['customer_id']}/entitlements"

    async def _present(self, effect: EffectView, ctx: AdapterContext) -> tuple[int | None, bool | None, list]:
        status, body = await self._read(ctx, self._base(effect))
        if status != 200:
            return status, None, []
        ents = body.get("entitlements", [])
        return status, effect.payload.get("entitlement", "premium") in ents, ents

    async def prepare(self, effect: EffectView, ctx: AdapterContext) -> PrepareResult:
        status, present, ents = await self._present(effect, ctx)
        if present is None:
            return PrepareResult(ok=False, reason=f"identity subject unreadable (status={status})")
        return PrepareResult(ok=True, observations={"had_entitlement": present, "entitlements": ents})

    async def execute(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult:
        ent = effect.payload.get("entitlement", "premium")
        return await self._send(ctx, "POST", f"{self._base(effect)}/{ent}/{self.op}", headers=ctx.headers(effect))

    async def _expect(self, effect: EffectView, ctx: AdapterContext, want_present: bool) -> VerificationResult:
        status, present, ents = await self._present(effect, ctx)
        if present is None:
            return unreadable(f"identity subject unreadable (status={status})")
        evidence = {"entitlements": ents}
        if present == want_present:
            return VerificationResult(status=VerificationStatus.VERIFIED_SUCCESS, evidence=evidence,
                                      external_reference=f"iam:{effect.payload['customer_id']}",
                                      reason=f"entitlement {'present' if want_present else 'absent'}")
        return VerificationResult(status=VerificationStatus.VERIFIED_FAILURE, evidence=evidence,
                                  reason=f"entitlement {'absent' if want_present else 'still present'}")

    async def verify(self, effect: EffectView, ctx: AdapterContext) -> VerificationResult:
        return await self._expect(effect, ctx, want_present=self.op == "grant")

    async def reconcile(self, effect: EffectView, ctx: AdapterContext) -> ReconciliationFinding:
        return await self._target_state_finding(await self.verify(effect, ctx))

    async def compensate(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult:
        ent = effect.payload.get("entitlement", "premium")
        return await self._send(ctx, "POST", f"{self._base(effect)}/{ent}/{self.inverse}",
                                headers=ctx.headers(effect, effect.provider_idempotency_key + "_comp"))

    async def verify_compensation(self, effect: EffectView, ctx: AdapterContext) -> VerificationResult:
        # Restore the pre-effect condition observed during prepare.
        had = effect.prepare_evidence.get("had_entitlement", self.op == "revoke")
        return await self._expect(effect, ctx, want_present=bool(had))
