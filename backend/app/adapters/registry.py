"""Effect Contract Registry: effect_type -> (contract, adapter)."""

from __future__ import annotations

from app.adapters.base import EffectAdapter
from app.adapters.billing_mock import BillingRefundAdapter
from app.adapters.crm_mock import CrmUpdateAdapter
from app.adapters.identity_mock import EntitlementAdapter
from app.adapters.notification_mock import NotificationAdapter
from app.adapters.subscription_mock import SubscriptionCancelAdapter
from app.domain.effect import EffectContract
from app.domain.errors import ValidationFailed


class EffectRegistry:
    def __init__(self, adapters: list[EffectAdapter]):
        self._adapters = {a.contract.effect_type: a for a in adapters}

    def has(self, effect_type: str) -> bool:
        return effect_type in self._adapters

    def contract(self, effect_type: str) -> EffectContract:
        try:
            return self._adapters[effect_type].contract
        except KeyError:
            raise ValidationFailed(f"no effect contract registered for {effect_type!r}",
                                   code="UNKNOWN_EFFECT_TYPE") from None

    def adapter(self, effect_type: str) -> EffectAdapter:
        self.contract(effect_type)
        return self._adapters[effect_type]

    def contracts(self) -> list[EffectContract]:
        return [self._adapters[k].contract for k in sorted(self._adapters)]


def default_registry() -> EffectRegistry:
    return EffectRegistry([
        BillingRefundAdapter(),
        SubscriptionCancelAdapter(),
        EntitlementAdapter("revoke"),
        EntitlementAdapter("grant"),
        CrmUpdateAdapter(),
        NotificationAdapter(),
    ])
