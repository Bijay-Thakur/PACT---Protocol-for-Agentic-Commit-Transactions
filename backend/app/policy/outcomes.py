"""Finite trusted required-outcome predicates.

Workflows may name only reviewed predicates from this registry.  No expression,
Python source, SQL, or model-generated evaluator is accepted at runtime.
"""

from __future__ import annotations

from typing import Any, Callable

from app.domain.enums import Application
from app.domain.errors import ValidationFailed

OutcomeEvaluator = Callable[[dict[str, Any], Any], tuple[bool, str]]


def _observed_refund_equals(requirement: dict[str, Any], observation: Any) -> tuple[bool, str]:
    passed = (
        observation.application == Application.APPLIED
        and str(observation.observed_amount) == str(requirement["amount"])
        and (not requirement.get("currency")
             or str((observation.evidence or {}).get("currency", requirement["currency"])).upper()
             == str(requirement["currency"]).upper())
    )
    return passed, (
        "observed application and amount match the frozen refund obligation"
        if passed else "final refund does not satisfy the frozen amount/currency obligation"
    )


_PREDICATES: dict[str, OutcomeEvaluator] = {
    "observed_refund_equals": _observed_refund_equals,
}


def evaluate_required_outcome(
    requirement: dict[str, Any], observation: Any
) -> tuple[bool, str]:
    predicate = str(requirement.get("predicate", ""))
    evaluator = _PREDICATES.get(predicate)
    if evaluator is None:
        raise ValidationFailed(
            f"required outcome predicate {predicate!r} is not registered",
            code="OUTCOME_PREDICATE_UNSUPPORTED",
        )
    return evaluator(requirement, observation)


def registered_outcome_predicates() -> tuple[str, ...]:
    return tuple(sorted(_PREDICATES))
