"""Notification adapter (contract notification.send v2.0.0). IRREVERSIBLE.

The recipient is resolved by trusted preparation (CRM email), and the content is
fingerprinted. Verification requires exactly one message for this operation with
the expected recipient, channel, template and content fingerprint, *delivered*
(``queued`` is accepted-not-delivered and keeps verification pending).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

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
    ReversibilityClass,
    VerificationStrategy,
)
from app.domain.resource_claim import ResourceClaim
from app.domain.verification import DispatchResult, Observation, PrepareResult

TEMPLATES = {"cancellation_confirmation", "cancellation_confirmation_no_refund", "refund_confirmation",
             "service_credit_notice"}


class NotificationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(min_length=1, max_length=128)
    template: str = Field(max_length=64)
    channel: Literal["email"] = "email"
    recipient: str | None = Field(default=None, max_length=256)
    variables: dict[str, Any] = Field(default_factory=dict)


def content_fingerprint(template: str, variables: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps({"t": template, "v": variables}, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


CONTRACT = EffectContract(
    effect_type="notification.send",
    adapter_name="notification_mock",
    provider="notification-sim",
    resource_type="customer.notifications",
    operation_kind="communication",
    required_capability="notification.send",
    reversibility_class=ReversibilityClass.IRREVERSIBLE,
    prepare_strategy=PrepareStrategy.READ_PRECONDITIONS,
    verification_strategy=VerificationStrategy.QUERY_BY_IDEMPOTENCY_KEY,
    compensation_strategy=CompensationStrategy.NONE,
    idempotency_strategy=IdempotencyStrategy.PROVIDER_IDEMPOTENCY_KEY,
    reconciliation_policy=ReconciliationPolicy.QUERY_BY_IDEMPOTENCY_KEY,
    evidence_requirements=["message_id", "to", "template", "content_fingerprint", "status", "match_count"],
    negative_evidence="AFTER_INFLIGHT_WINDOW",
    idempotency_window_s=86400.0,
    safe_retry_statuses={},
    required_delivery="delivered",
    restoration_scope="none: a delivered message cannot be unsent",
    description="Send a customer-facing message to the CRM-resolved recipient.",
    payload_model=NotificationPayload,
    required_claims=lambda p: [
        ResourceClaim(resource=f"notification/customer:{p['customer_id']}/messages", mode=ClaimMode.WRITE)
    ],
)


class NotificationAdapter(HttpAdapter):
    contract = CONTRACT

    async def prepare(self, effect: EffectView, ctx: AdapterContext) -> PrepareResult:
        template = effect.payload["template"]
        if template not in TEMPLATES:
            return PrepareResult(ok=False, reason=f"unknown template {template!r}")
        status, body = await self._read(ctx, f"/sim/crm/accounts/{effect.payload['customer_id']}")
        if status != 200 or not body.get("email"):
            return PrepareResult(ok=False, reason="recipient could not be resolved from the CRM")
        return PrepareResult(ok=True, observations={"template": template, "recipient": body["email"]},
                             resolved={"recipient": body["email"]},
                             preconditions={"recipient": body["email"]})

    async def execute(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult:
        p = effect.payload
        return await self._send(ctx, "POST", "/sim/notification/messages", headers=ctx.headers(effect), json={
            "customer_id": p["customer_id"], "to": p["recipient"], "template": p["template"],
            "channel": p.get("channel", "email"), "variables": p.get("variables", {}),
            "metadata": {"pact_operation": effect.operation_key,
                         "content_fingerprint": content_fingerprint(p["template"], p.get("variables", {}))},
        })

    async def observe(self, effect: EffectView, ctx: AdapterContext) -> Observation:
        src = "notification-sim:/notification/messages?pact_operation"
        p = effect.payload
        status, body = await self._read(ctx, "/sim/notification/messages", params={
            "customer_id": p["customer_id"], "pact_operation": effect.operation_key})
        if status != 200:
            return unreadable("message lookup failed", source=src)
        msgs = body.get("data", [])
        if not msgs:
            return Observation(application=Application.UNKNOWN, postcondition=Postcondition.UNDETERMINED,
                               absent=True, source=src, evidence={"match_count": 0}, reason="no message for operation")
        fp = content_fingerprint(p["template"], p.get("variables", {}))
        problems = []
        for m in msgs:
            for field, want in (("to", p.get("recipient")), ("template", p["template"]),
                                ("channel", p.get("channel", "email"))):
                if m.get(field) != want:
                    problems.append({"message_id": m["id"], "field": field, "expected": want, "observed": m.get(field)})
            got_fp = content_fingerprint(m.get("template", ""), m.get("variables", {}))
            if got_fp != fp:
                problems.append({"message_id": m["id"], "field": "content_fingerprint"})
        evidence = {"match_count": len(msgs), "messages": [{k: m.get(k) for k in ("id", "to", "template", "status")}
                                                           for m in msgs], "problems": problems,
                    "content_fingerprint": fp}
        if len(msgs) > 1:
            return Observation(application=Application.APPLIED, postcondition=Postcondition.MISMATCH, source=src,
                               evidence=evidence, provider_reference=msgs[0]["id"],
                               reason=f"{len(msgs)} messages for one operation (duplicate)")
        if problems:
            return Observation(application=Application.APPLIED, postcondition=Postcondition.MISMATCH, source=src,
                               evidence=evidence, provider_reference=msgs[0]["id"],
                               reason="message sent but " + ", ".join(sorted({x["field"] for x in problems}))
                                      + " does not match")
        if msgs[0].get("status") != self.contract.required_delivery:
            return Observation(application=Application.APPLIED, postcondition=Postcondition.UNDETERMINED,
                               pending=True, source=src, evidence=evidence, provider_reference=msgs[0]["id"],
                               reason=f"accepted but {msgs[0].get('status')}, not yet delivered")
        return Observation(application=Application.APPLIED, postcondition=Postcondition.MATCH, source=src,
                           evidence=evidence, provider_reference=msgs[0]["id"],
                           reason="exactly one delivered message with expected recipient, template and content")
