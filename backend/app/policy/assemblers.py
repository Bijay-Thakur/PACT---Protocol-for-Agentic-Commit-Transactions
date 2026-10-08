"""Trusted, explicitly registered workflow assemblers.

Assemblers are domain policy extensions.  Generic HTTP routes dispatch through
this registry and never branch on a workflow name.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from sqlalchemy import select

from app.domain.effect import EffectProposal
from app.domain.errors import StateConflict, ValidationFailed
from app.persistence.models import EffectRow, IntentAcceptanceRow, TransactionRow
from app.persistence.repositories import effect_view
from app.security.principals import Principal

if TYPE_CHECKING:
    from app.runtime import Runtime


class TrustedAssembler(Protocol):
    workflow_key: str

    async def assemble(self, root_id: UUID, principal: Principal, runtime: "Runtime") -> dict[str, Any]: ...


class AssemblyRegistry:
    def __init__(self, assemblers: list[TrustedAssembler] | None = None):
        self._assemblers = {item.workflow_key: item for item in assemblers or []}

    def register(self, assembler: TrustedAssembler) -> None:
        if assembler.workflow_key in self._assemblers:
            raise ValueError(f"assembler already registered for {assembler.workflow_key}")
        self._assemblers[assembler.workflow_key] = assembler

    def supports(self, workflow_key: str) -> bool:
        return workflow_key in self._assemblers

    async def assemble(
        self, workflow_key: str, root_id: UUID, principal: Principal, runtime: "Runtime"
    ) -> dict[str, Any]:
        assembler = self._assemblers.get(workflow_key)
        if assembler is None:
            raise StateConflict(
                "workflow requires explicit effect proposals",
                code="TRUSTED_ASSEMBLER_NOT_REGISTERED",
            )
        return await assembler.assemble(root_id, principal, runtime)


class OffboardingAssembler:
    workflow_key = "customer_offboarding"

    async def assemble(
        self, root_id: UUID, principal: Principal, runtime: "Runtime"
    ) -> dict[str, Any]:
        async with runtime.db.read() as session:
            root = await session.get(TransactionRow, root_id)
            link = (await session.execute(select(IntentAcceptanceRow).where(
                IntentAcceptanceRow.root_id == root_id))).scalar_one_or_none()
            if root is None or root.tenant_id != principal.tenant_id \
                    or root.principal_id != principal.id or link is None:
                raise StateConflict("accepted draft unavailable", code="DRAFT_NOT_AVAILABLE")
            cid = link.clarified_request["customer_id"]
            reason = link.clarified_request.get("reason", "customer requested cancellation")

        async def add(slot: str, effect_type: str, payload: dict[str, Any]) -> UUID:
            effect_id, _ = await runtime.manager.propose(
                principal,
                root_id,
                EffectProposal(effect_type=effect_type, slot=slot, payload=payload),
                request_id=f"assemble:{root_id}:{slot}",
            )
            return effect_id

        cancel_id = await add(
            "cancel_subscription", "subscription.cancel",
            {"customer_id": cid, "reason": reason},
        )
        async with runtime.db.read() as session:
            cancel = await session.get(EffectRow, cancel_id)
            view = effect_view(cancel)
        fact = await runtime.registry.adapter("subscription.cancel").prepare(
            view, runtime.exec_ctx.adapter_ctx()
        )
        if not fact.ok or "unused_balance" not in fact.observations:
            return {
                "transaction_id": str(root_id),
                "status": "NEEDS_CLARIFICATION",
                "issues": [{"code": "FACT_UNAVAILABLE", "detail": fact.reason}],
                "applied": False,
            }
        try:
            eligible = Decimal(str(fact.observations["unused_balance"]))
        except InvalidOperation:
            raise ValidationFailed("trusted unused balance invalid", code="FACT_UNAVAILABLE") from None
        if eligible < 0:
            raise ValidationFailed("trusted unused balance invalid", code="FACT_UNAVAILABLE")
        await add("revoke_premium", "identity.revoke", {
            "customer_id": cid, "entitlement": "premium",
        })
        await add("mark_churned", "crm.update", {
            "customer_id": cid, "lifecycle_state": "churned", "note": "Cancelled via PACT",
        })
        if eligible > 0:
            await add("refund_unused", "billing.refund", {
                "customer_id": cid, "amount": str(eligible), "reason": "unused-period refund",
            })
        await add("confirm_customer", "notification.send", {
            "customer_id": cid,
            "template": "cancellation_confirmation",
            "variables": {"refund_amount": str(eligible)},
        })
        return {
            "transaction_id": str(root_id),
            "status": "ASSEMBLED",
            "trusted_eligible_refund": str(eligible),
            "applied": False,
            "next_action": "PREPARE",
        }
