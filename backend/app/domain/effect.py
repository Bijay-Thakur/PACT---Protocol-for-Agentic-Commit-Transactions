from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import (
    Application,
    CommitStrategy,
    CompensationStrategy,
    Consistency,
    EffectState,
    IdempotencyStrategy,
    Postcondition,
    PrepareStrategy,
    ReconciliationPolicy,
    Restoration,
    ReversibilityClass,
    VerificationStrategy,
)
from app.domain.resource_claim import ResourceClaim


class EffectProposal(BaseModel):
    """An agent's proposal of one external side effect. Proposing never executes.

    ``operation_key`` is a *client hint* only. The business operation identity is
    derived server-side at compile time (tenant / provider / business request /
    action slot / canonical resource / epoch). ``actor_id`` is optional and, if
    present, must equal the authenticated principal; it never grants identity.
    """

    model_config = ConfigDict(extra="forbid")

    effect_type: str = Field(min_length=3, max_length=128)
    actor_id: str | None = Field(default=None, max_length=128)
    operation_key: str | None = Field(default=None, min_length=3, max_length=512,
                                      pattern=r"^[A-Za-z0-9_.:\-/@#]+$")
    slot: str | None = Field(default=None, pattern=r"^[a-z0-9_]{2,64}$")
    payload: dict[str, Any] = Field(default_factory=dict)
    resource_claims: list[ResourceClaim] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list, description="operation_keys or slot names")
    expected_draft_version: int | None = Field(default=None, ge=0)


NegativeEvidence = Literal["AUTHORITATIVE", "AFTER_INFLIGHT_WINDOW", "NONE"]


class EffectContract(BaseModel):
    """Describes how an effect behaves *transactionally*, not just how to call it.

    Phase 2 adds explicit, versioned provider guarantees. Running effects are
    pinned to the contract id/version/hash recorded at compile time.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    effect_type: str
    contract_version: str = "2.0.0"
    adapter_name: str
    provider: str
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
    supported_currencies: list[str] = Field(default_factory=list)
    contradicts: list[str] = Field(default_factory=list)
    # --- provider guarantees (Phase 2) ---------------------------------
    consistency: Consistency = Consistency.STRONG
    consistency_lag_s: float = 0.0
    max_inflight_s: float = 2.0
    negative_evidence: NegativeEvidence = "AFTER_INFLIGHT_WINDOW"
    idempotency_window_s: float | None = None
    # HTTP statuses that prove the provider did NOT apply the request.
    definitive_rejection_statuses: list[int] = Field(default_factory=lambda: [400, 404, 409, 412, 422])
    # HTTP statuses after which a retry is safe WITHOUT reconciliation, with the
    # contract-level justification. Anything else ambiguous goes to UNKNOWN.
    safe_retry_statuses: dict[int, str] = Field(default_factory=dict)
    restoration_scope: str = ""
    retained_history: list[str] = Field(default_factory=list)
    required_delivery: str | None = None
    description: str = ""
    payload_model: Any = Field(default=None, exclude=True)
    required_claims: Callable[[dict[str, Any]], list[ResourceClaim]] | None = Field(default=None, exclude=True)

    @property
    def contract_id(self) -> str:
        return self.effect_type

    @property
    def compensable(self) -> bool:
        return self.reversibility_class in (
            ReversibilityClass.REVERSIBLE,
            ReversibilityClass.COMPENSABLE,
        ) and self.compensation_strategy != CompensationStrategy.NONE

    def public(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"payload_model", "required_claims"})

    @property
    def contract_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.public(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class EffectView(BaseModel):
    """Read model of an effect handed to adapters and API clients.

    Adapters receive this immutable view - never a database session.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: str = "demo"
    transaction_id: UUID
    root_id: UUID
    logical_operation_id: UUID | None
    operation_key: str
    slot: str | None = None
    effect_type: str
    contract_version: str | None = None
    actor_id: str
    state: EffectState
    payload: dict[str, Any]
    amount: Decimal | None
    currency: str | None = None
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
    application: Application = Application.NOT_SENT
    postcondition: Postcondition = Postcondition.UNDETERMINED
    restoration: Restoration = Restoration.NOT_REQUIRED
    observed_amount: Decimal | None = None
    max_exposure: Decimal | None = None
    created_at: datetime
    updated_at: datetime
    dispatched_at: datetime | None = None
    verified_at: datetime | None = None
