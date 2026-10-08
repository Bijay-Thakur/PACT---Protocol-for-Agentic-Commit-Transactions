"""Trusted extension registration must choose one owner per public key."""

import pytest

from app.adapters.billing_mock import BillingRefundAdapter
from app.adapters.registry import EffectRegistry
from app.domain.errors import ValidationFailed
from app.policy.workflows import CustomerOffboarding, WorkflowRegistry


@pytest.mark.parametrize("initial", [True, False])
def test_two_adapters_for_same_effect_type_are_rejected(initial):
    first = BillingRefundAdapter()
    second = BillingRefundAdapter()
    if initial:
        with pytest.raises(ValidationFailed) as error:
            EffectRegistry([first, second])
    else:
        registry = EffectRegistry([first])
        with pytest.raises(ValidationFailed) as error:
            registry.register(second)
        assert registry.adapter(first.contract.effect_type) is first
    assert error.value.code == "AMBIGUOUS_ADAPTER_REGISTRATION"


@pytest.mark.parametrize("initial", [True, False])
def test_two_workflows_for_same_key_are_rejected(initial):
    first = CustomerOffboarding()
    second = CustomerOffboarding()
    if initial:
        with pytest.raises(ValidationFailed) as error:
            WorkflowRegistry([first, second])
    else:
        registry = WorkflowRegistry([first])
        with pytest.raises(ValidationFailed) as error:
            registry.register(second)
        assert registry.get(first.key) is first
    assert error.value.code == "AMBIGUOUS_WORKFLOW_REGISTRATION"
