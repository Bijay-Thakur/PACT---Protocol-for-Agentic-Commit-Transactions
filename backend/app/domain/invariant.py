from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.enums import FailureAction, InvariantPhase, Severity

ExpressionType = Literal[
    "effect_amount_lte",
    "sum_amount_lte",
    "field_matches",
    "unique_operation_keys",
    "depends_on_verified",
    "implies_verified",
    "single_external_match",
]


class InvariantDefinition(BaseModel):
    """A deterministic, declarative transaction-wide constraint.

    ``config`` is interpreted by a code-backed evaluator selected by
    ``expression_type``; no model output participates in evaluation.
    """

    key: str = Field(pattern=r"^[a-z0-9_]{2,64}$")
    name: str
    phase: InvariantPhase
    expression_type: ExpressionType
    config: dict[str, Any] = Field(default_factory=dict)
    severity: Severity = Severity.CRITICAL
    failure_action: FailureAction
    description: str = ""


class InvariantEvaluation(BaseModel):
    invariant_id: UUID | None = None
    invariant_key: str
    name: str
    phase: InvariantPhase
    passed: bool
    observed_values: dict[str, Any] = Field(default_factory=dict)
    reason: str
    failure_action: FailureAction
    evaluated_at: datetime | None = None
