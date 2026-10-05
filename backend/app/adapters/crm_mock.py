"""CRM adapter (contract crm.update v2.0.0). REVERSIBLE for lifecycle state only.

Mutated fields: ``lifecycle_state`` and one appended note tagged with the PACT
operation identity. The CRM keeps notes as append-only history, so restoration
can restore ``lifecycle_state`` but cannot remove the note; the contract
declares the note as RETAINED HISTORY (policy-accepted residual, non-blocking).
Postcondition requires both the target state and exactly one tagged note.
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


class CrmUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(min_length=1, max_length=128)
    lifecycle_state: str = Field(pattern=r"^[a-z_]{3,48}$")
    note: str | None = Field(default=None, max_length=500)


CONTRACT = EffectContract(
    effect_type="crm.update",
    adapter_name="crm_mock",
    provider="crm-sim",
    resource_type="customer.crm_record",
    operation_kind="record_update",
    required_capability="crm.update",
    reversibility_class=ReversibilityClass.REVERSIBLE,
    prepare_strategy=PrepareStrategy.READ_PRECONDITIONS,
    verification_strategy=VerificationStrategy.QUERY_TARGET_STATE,
    compensation_strategy=CompensationStrategy.RESTORE_BEFORE_IMAGE,
    idempotency_strategy=IdempotencyStrategy.TARGET_STATE_IDEMPOTENT,
    reconciliation_policy=ReconciliationPolicy.QUERY_TARGET_STATE,
    evidence_requirements=["lifecycle_state", "tagged_note_count", "version"],
    negative_evidence="AFTER_INFLIGHT_WINDOW",
    # Note appends are not idempotent without the provider key record; 503 is ambiguous -> reconcile first.
    safe_retry_statuses={},
    restoration_scope="lifecycle_state restored to the before-image if no later change exists",
    retained_history=["notes"],
    description="Update the CRM lifecycle state and append a tagged note.",
    payload_model=CrmUpdatePayload,
    required_claims=lambda p: [
        ResourceClaim(resource=f"crm/customer:{p['customer_id']}/account", mode=ClaimMode.WRITE)
    ],
)


def note_tag(effect: EffectView) -> str:
    return f"[pact:{effect.provider_idempotency_key}]"


class CrmUpdateAdapter(HttpAdapter):
    contract = CONTRACT

    def _path(self, effect: EffectView) -> str:
        return f"/sim/crm/accounts/{effect.payload['customer_id']}"

    async def _get(self, effect: EffectView, ctx: AdapterContext) -> tuple[int | None, dict]:
        status, body = await self._read(ctx, self._path(effect))
        return status, body if isinstance(body, dict) else {}

    async def prepare(self, effect: EffectView, ctx: AdapterContext) -> PrepareResult:
        status, body = await self._get(effect, ctx)
        if status != 200:
            return PrepareResult(ok=False, reason=f"crm account unreadable (status={status})")
        return PrepareResult(
            ok=True,
            observations={"before_image": {"lifecycle_state": body["lifecycle_state"]}, "email": body.get("email"),
                          "version": body["version"]},
            preconditions={"lifecycle_state": body["lifecycle_state"], "version": body["version"]},
        )

    async def execute(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult:
        version = (effect.prepare_evidence or {}).get("preconditions", {}).get("version")
        note = f"{effect.payload.get('note') or 'updated by PACT'} {note_tag(effect)}"
        return await self._send(ctx, "PATCH", self._path(effect), headers=ctx.headers(effect, if_match=version),
                                json={"lifecycle_state": effect.payload["lifecycle_state"], "note": note})

    async def observe(self, effect: EffectView, ctx: AdapterContext) -> Observation:
        src = "crm-sim:/crm/accounts"
        status, body = await self._get(effect, ctx)
        if status != 200:
            return unreadable(f"crm account unreadable (status={status})", source=src)
        target = effect.payload["lifecycle_state"]
        before = (effect.prepare_evidence or {}).get("observations", {}).get("before_image", {}).get("lifecycle_state")
        tagged = [n for n in body.get("notes", []) if note_tag(effect) in n]
        mine = [h for h in body.get("history", []) if h.get("key") == effect.provider_idempotency_key]
        state_ok = body["lifecycle_state"] == target
        evidence = {"lifecycle_state": body["lifecycle_state"], "tagged_note_count": len(tagged),
                    "version": body["version"], "history_entries_for_operation": mine}
        if mine:
            application = Application.APPLIED
        elif state_ok or tagged:
            application = Application.UNKNOWN
        else:
            application = Application.NOT_APPLIED_CONFIRMED  # strongly consistent: unchanged, no record
        if state_ok and len(tagged) == 1:
            post, reason = Postcondition.MATCH, f"lifecycle_state={target} with one tagged note"
        elif state_ok and len(tagged) == 0:
            post, reason = Postcondition.PARTIAL, "lifecycle_state updated but the tagged note is missing"
        elif len(tagged) > 1 or len(mine) > 1:
            post, reason = Postcondition.MISMATCH, f"{max(len(tagged), len(mine))} applications (duplicate)"
        elif tagged:
            post, reason = Postcondition.PARTIAL, f"note written but lifecycle_state is {body['lifecycle_state']}"
        else:
            post = Postcondition.MISMATCH
            reason = f"lifecycle_state is {body['lifecycle_state']} (before-image {before}), expected {target}"
        return Observation(application=application, postcondition=post, evidence=evidence, source=src,
                           provider_reference=f"crm:{effect.payload['customer_id']}@v{body['version']}",
                           reason=reason)

    async def restore(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult:
        before = (effect.prepare_evidence or {}).get("observations", {}).get("before_image", {}).get("lifecycle_state")
        ev = (effect.verification_result or {}).get("evidence", {})
        entries = ev.get("history_entries_for_operation") or []
        produced = entries[-1].get("version") if entries else ev.get("version")
        return await self._send(ctx, "PATCH", self._path(effect),
                                headers=ctx.headers(effect, effect.provider_idempotency_key + "_restore",
                                                    if_match=produced),
                                json={"lifecycle_state": before})

    async def observe_restoration(self, effect: EffectView, ctx: AdapterContext) -> Observation:
        src = "crm-sim:/crm/accounts"
        status, body = await self._get(effect, ctx)
        if status != 200:
            return unreadable(f"crm account unreadable (status={status})", source=src)
        before = (effect.prepare_evidence or {}).get("observations", {}).get("before_image", {}).get("lifecycle_state")
        ok = body["lifecycle_state"] == before
        return Observation(application=Application.APPLIED, postcondition=Postcondition.MATCH,
                           restoration=Restoration.RESTORED if ok else Restoration.RESIDUAL,
                           evidence={"lifecycle_state": body["lifecycle_state"], "version": body["version"],
                                     "retained_notes": [n for n in body.get("notes", []) if note_tag(effect) in n]},
                           source=src, reason="lifecycle_state restored; tagged note retained as history"
                           if ok else f"lifecycle_state is {body['lifecycle_state']}, expected {before}")
