"""Effect Contract Registry: effect_type -> (contract, adapter), version-pinned.

Effects persist the contract id/version/hash they were compiled against. A
running effect never silently adopts a newly deployed contract: if its pinned
version is not supported by this deployment, it is held for operator migration
(``UnsupportedContractVersion``) instead of guessing recovery semantics.
"""

from __future__ import annotations

from app.adapters.base import EffectAdapter
from app.adapters.billing_mock import BillingRefundAdapter
from app.adapters.crm_mock import CrmUpdateAdapter
from app.adapters.identity_mock import EntitlementAdapter
from app.adapters.notification_mock import NotificationAdapter
from app.adapters.subscription_mock import SubscriptionCancelAdapter
from app.domain.effect import EffectContract
from app.domain.errors import StateConflict, ValidationFailed


class UnsupportedContractVersion(StateConflict):
    code = "UNSUPPORTED_CONTRACT_VERSION"


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

    def pinned(self, effect_type: str, version: str | None, contract_hash: str | None = None) -> EffectAdapter:
        """Adapter for an effect pinned to ``version`` (and optionally an exact contract hash)."""
        adapter = self.adapter(effect_type)
        c = adapter.contract
        if version != c.contract_version or (contract_hash is not None and contract_hash != c.contract_hash):
            raise UnsupportedContractVersion(
                f"{effect_type} is pinned to contract {version} ({(contract_hash or '')[:12]}); this deployment "
                f"provides {c.contract_version} ({c.contract_hash[:12]}). Held for operator migration.",
                details={"effect_type": effect_type, "pinned_version": version, "deployed_version": c.contract_version})
        return adapter

    def contracts(self) -> list[EffectContract]:
        return [self._adapters[k].contract for k in sorted(self._adapters)]

    def register(self, adapter: EffectAdapter) -> None:
        self._adapters[adapter.contract.effect_type] = adapter


def default_registry() -> EffectRegistry:
    return EffectRegistry([
        BillingRefundAdapter(),
        SubscriptionCancelAdapter(),
        EntitlementAdapter("revoke"),
        EntitlementAdapter("grant"),
        CrmUpdateAdapter(),
        NotificationAdapter(),
    ])
