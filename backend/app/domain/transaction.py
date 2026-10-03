from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.capability import CapabilitySpec, RootCapabilityGrant
from app.domain.effect import EffectProposal
from app.domain.enums import TransactionState
from app.domain.invariant import InvariantDefinition


class RecoveryPolicy(BaseModel):
    on_effect_failure: Literal["COMPENSATE", "HUMAN_REQUIRED"] = "COMPENSATE"
    on_compensation_failure: Literal["HUMAN_REQUIRED"] = "HUMAN_REQUIRED"
    on_unknown: Literal["RECONCILE", "HUMAN_REQUIRED"] = "RECONCILE"
    auto_reconcile: bool = Field(
        default=False,
        description="Reconcile UNKNOWN effects immediately within the commit flow",
    )
    max_auto_reconcile_rounds: int = Field(default=3, ge=0, le=10)


class ChildSpec(BaseModel):
    actor_id: str = Field(min_length=1, max_length=128)
    objective: str = Field(min_length=1, max_length=2000)
    capability: CapabilitySpec
    required: bool = True
    effects: list[EffectProposal] = Field(default_factory=list)
    invariants: list[InvariantDefinition] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TransactionSpec(BaseModel):
    """Protocol object describing a root business transaction.

    May be produced by a human, a scripted agent, or a model-backed planner. In all
    cases it is only a *proposal*: every element is validated by PACT.
    """

    objective: str = Field(min_length=1, max_length=2000)
    actor_id: str = Field(min_length=1, max_length=128)
    capability: RootCapabilityGrant
    participants: list[str] = Field(default_factory=list)
    effects: list[EffectProposal] = Field(default_factory=list)
    invariants: list[InvariantDefinition] = Field(default_factory=list)
    children: list[ChildSpec] = Field(default_factory=list)
    required_effect_types: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    recovery_policy: RecoveryPolicy = Field(default_factory=RecoveryPolicy)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TransactionSummary(BaseModel):
    id: UUID
    root_id: UUID
    parent_id: UUID | None
    objective: str
    actor_id: str
    state: TransactionState
    required: bool
    child_count: int
    effect_count: int
    invariant_status: Literal["PASSED", "FAILED", "NOT_EVALUATED"]
    created_at: datetime
    updated_at: datetime
    finalized_at: datetime | None
    version: int
