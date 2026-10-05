"""Identity / entitlement adapter (contracts identity.revoke / identity.grant v2.0.0).

- Revoke-already-absent and grant-already-present are provider no-ops: the
  postcondition holds, nothing was applied, and nothing must be restored (A24).
- Restoration only reverses a change this operation actually made, and only if
  the record still carries the version PACT produced.
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


class EntitlementPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(min_length=1, max_length=128)
    entitlement: str = Field(default="premium", pattern=r"^[a-z0-9_\-]{2,32}$")


def _claims(p: dict) -> list[ResourceClaim]:
    return [ResourceClaim(resource=f"identity/customer:{p['customer_id']}/entitlement:{p.get('entitlement', 'premium')}",
                          mode=ClaimMode.WRITE)]


def _contract(op: str, inverse: str) -> EffectContract:
    return EffectContract(
        effect_type=f"identity.{op}",
        adapter_name="identity_mock",
        provider="identity-sim",
        resource_type="customer.entitlement",
        operation_kind="access_change",
        required_capability=f"identity.{op}",
        reversibility_class=ReversibilityClass.COMPENSABLE,
        prepare_strategy=PrepareStrategy.READ_PRECONDITIONS,
        verification_strategy=VerificationStrategy.QUERY_TARGET_STATE,
        compensation_strategy=CompensationStrategy.INVERSE_OPERATION,
        idempotency_strategy=IdempotencyStrategy.TARGET_STATE_IDEMPOTENT,
        reconciliation_policy=ReconciliationPolicy.QUERY_TARGET_STATE,
        evidence_requirements=["entitlements", "version", "history"],
        negative_evidence="AFTER_INFLIGHT_WINDOW",
        safe_retry_statuses={503: "TARGET_STATE_IDEMPOTENT: the provider records the key; a replay is a no-op"},
        contradicts=[f"identity.{inverse}"],
        restoration_scope=f"{inverse} only if this operation changed the entitlement and no later change exists",
        description=f"{op.capitalize()} a customer entitlement (restoration: conditional {inverse}).",
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

    def _ent(self, effect: EffectView) -> str:
        return effect.payload.get("entitlement", "premium")

    async def _get(self, effect: EffectView, ctx: AdapterContext) -> tuple[int | None, dict]:
        status, body = await self._read(ctx, self._base(effect))
        return status, body if isinstance(body, dict) else {}

    async def prepare(self, effect: EffectView, ctx: AdapterContext) -> PrepareResult:
        status, body = await self._get(effect, ctx)
        if status != 200:
            return PrepareResult(ok=False, reason=f"identity subject unreadable (status={status})")
        present = self._ent(effect) in body.get("entitlements", [])
        return PrepareResult(ok=True,
                             observations={"had_entitlement": present, "entitlements": body["entitlements"],
                                           "version": body["version"]},
                             preconditions={"had_entitlement": present, "version": body["version"]})

    async def execute(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult:
        version = (effect.prepare_evidence or {}).get("preconditions", {}).get("version")
        return await self._send(ctx, "POST", f"{self._base(effect)}/{self._ent(effect)}/{self.op}",
                                headers=ctx.headers(effect, if_match=version))

    async def observe(self, effect: EffectView, ctx: AdapterContext) -> Observation:
        src = "identity-sim:/identity/customers/entitlements"
        status, body = await self._get(effect, ctx)
        if status != 200:
            return unreadable(f"identity subject unreadable (status={status})", source=src)
        present = self._ent(effect) in body.get("entitlements", [])
        want_present = self.op == "grant"
        mine = [h for h in body.get("history", []) if h.get("key") == effect.provider_idempotency_key
                and h.get("op") == self.op]
        if mine and mine[-1].get("changed"):
            application = Application.APPLIED
        elif mine:
            application = Application.NOT_APPLIED_CONFIRMED  # recorded no-op (already in target state)
        elif present == want_present:
            application = Application.UNKNOWN
        else:
            application = Application.NOT_APPLIED_CONFIRMED
        post = Postcondition.MATCH if present == want_present else Postcondition.MISMATCH
        return Observation(application=application, postcondition=post, source=src,
                           evidence={"entitlements": body["entitlements"], "version": body["version"],
                                     "history_entries_for_operation": mine},
                           provider_reference=f"iam:{effect.payload['customer_id']}@v{body['version']}",
                           reason=f"entitlement {'present' if present else 'absent'}"
                                  + (" (provider recorded a no-op)" if mine and not mine[-1].get("changed") else ""))

    async def restore(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult:
        ev = (effect.verification_result or {}).get("evidence", {})
        entries = ev.get("history_entries_for_operation") or []
        produced = entries[-1].get("version") if entries else None
        return await self._send(ctx, "POST", f"{self._base(effect)}/{self._ent(effect)}/{self.inverse}",
                                headers=ctx.headers(effect, effect.provider_idempotency_key + "_restore",
                                                    if_match=produced))

    async def observe_restoration(self, effect: EffectView, ctx: AdapterContext) -> Observation:
        src = "identity-sim:/identity/customers/entitlements"
        status, body = await self._get(effect, ctx)
        if status != 200:
            return unreadable(f"identity subject unreadable (status={status})", source=src)
        had = bool((effect.prepare_evidence or {}).get("observations", {}).get("had_entitlement"))
        present = self._ent(effect) in body.get("entitlements", [])
        return Observation(application=Application.APPLIED, postcondition=Postcondition.MATCH,
                           restoration=Restoration.RESTORED if present == had else Restoration.RESIDUAL,
                           evidence={"entitlements": body["entitlements"], "version": body["version"]}, source=src,
                           reason="entitlement back to its before-image" if present == had else "not restored")
