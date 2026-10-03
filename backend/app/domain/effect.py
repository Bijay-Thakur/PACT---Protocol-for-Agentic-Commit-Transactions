from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Callable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import (
    CommitStrategy,
    CompensationStrategy,
    EffectState,
    IdempotencyStrategy,
    PrepareStrategy,
    ReconciliationPolicy,
    ReversibilityClass,
    VerificationStrategy,
)
from app.domain.resource_claim import ResourceClaim


class EffectProposal(BaseModel):
    """An agent's proposal of one external side effect. Proposing never executes."""

    effect_type: str = Field(min_length=3, max_length=128)
    actor_id: str = Field(min_length=1, max_length=128)
    operation_key: str = Field(min_length=3, max_length=512, pattern=r"^[A-Za-z0-9_.:\-/@#]+$")
    payload: dict[str, Any] = Field(default_factory=dict)
    resource_claims: list[ResourceClaim] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list, description="operation_keys")


class EffectContract(BaseModel):
    """Describes how an effect behaves *transactionally*, not just how to call it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    effect_type: str
    adapter_name: str
    resource_type: str
    operation_kind: str
    required_capability: str
    reversibility_class: ReversibilityClass
    prepare_strategy: PrepareStrategy
    commit_strategy: CommitStrategy = CommitStrategy.SINGLE_DISPATCH
    verification_strategy: VerificationStrategy
    compensation_strategy: CompensationStrategy
    idempotency_strategy: IdempotencyStrategy
    timeout_seconds: float = 1.0
    max_attempts: int = 3
    verification_polls: int = 6
    verification_poll_interval_s: float = 0.15
    reconciliation_policy: ReconciliationPolicy
    evidence_requirements: list[str] = Field(default_factory=list)
    amount_field: str | None = None
    contradicts: list[str] = Field(default_factory=list)
    description: str = ""
    payload_model: Any = Field(default=None, exclude=True)
    required_claims: Callable[[dict[str, Any]], list[ResourceClaim]] | None = Field(
        default=None, exclude=True
    )

    @property
    def compensable(self) -> bool:
        return self.reversibility_class in (
            ReversibilityClass.REVERSIBLE,
            ReversibilityClass.COMPENSABLE,
        ) and self.compensation_strategy != CompensationStrategy.NONE

    def public(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"payload_model", "required_claims"})


class EffectView(BaseModel):
    """Read model of an effect handed to adapters and API clients.

    Adapters receive this immutable view - never a database session.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    transaction_id: UUID
    root_id: UUID
    logical_operation_id: UUID
    operation_key: str
    effect_type: str
    actor_id: str
    state: EffectState
    payload: dict[str, Any]
    amount: Decimal | None
    resource_claims: list[ResourceClaim]
    depends_on: list[str]
    reversibility_class: ReversibilityClass
    provider_idempotency_key: str
    provider_reference: str | None
    prepare_evidence: dict[str, Any] = Field(default_factory=dict)
    dispatch_result: dict[str, Any] | None = None
    verification_result: dict[str, Any] | None = None
    reconciliation_result: dict[str, Any] | None = None
    compensation_result: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
    dispatched_at: datetime | None = None
    verified_at: datetime | None = None
