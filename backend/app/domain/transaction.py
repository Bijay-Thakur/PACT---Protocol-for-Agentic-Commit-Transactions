"""Request/response protocol objects for transactions (Phase 2).

Identity is never taken from these bodies. Fields such as ``actor_id``,
``issuer``, ``tenant_id`` and ``operator_id`` are accepted only so a mismatch with
the authenticated principal can be rejected loudly as a forgery (A02).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.capability import CapabilitySpec


class RecoveryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on_effect_failure: Literal["COMPENSATE", "HUMAN_REQUIRED"] = "COMPENSATE"
    on_compensation_failure: Literal["HUMAN_REQUIRED"] = "HUMAN_REQUIRED"
    on_unknown: Literal["RECONCILE", "HUMAN_REQUIRED"] = "RECONCILE"
    auto_reconcile: bool = Field(default=True, description="Reconcile UNKNOWN effects automatically")
    max_auto_reconcile_rounds: int = Field(default=6, ge=0, le=20)


class BeginRequest(BaseModel):
    """Start a draft for an authorized workflow and a business request."""

    model_config = ConfigDict(extra="forbid")

    workflow: str = Field(min_length=3, max_length=64)
    business_request: dict[str, Any]
    objective: str | None = Field(default=None, max_length=2000)
    new_business_epoch: bool = False
    epoch_reason: str | None = Field(default=None, max_length=500)
    recovery_policy: RecoveryPolicy = Field(default_factory=RecoveryPolicy)
    labels: dict[str, str] = Field(default_factory=dict, description="non-authoritative display labels")
    # Identity assertions are never trusted; present only to reject forgeries.
    actor_id: str | None = None
    issuer: str | None = None
    tenant_id: str | None = None


class DelegateRequest(BaseModel):
    """Request narrower child authority for a *verified* recipient principal."""

    model_config = ConfigDict(extra="forbid")

    recipient: str = Field(min_length=1, max_length=128)
    objective: str = Field(min_length=1, max_length=2000)
    capability: CapabilitySpec
    required: bool = True
    actor_id: str | None = None
    tenant_id: str | None = None


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=3, max_length=2000)
    operator_id: str | None = None


class CommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    step_delay_ms: int = Field(default=0, ge=0, le=3000, description="demo pacing only")


class AbortRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=2000)


class ReviseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=2000)


class WithdrawRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=500)


class OperatorActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["RECONCILE", "RETRY_RESTORATION", "ATTEST_RESIDUAL", "FINALIZE_FAILED", "RELEASE_QUARANTINE_READONLY"]
    reason: str = Field(min_length=3, max_length=2000)
    residual_id: str | None = None
    evidence_reference: str | None = Field(default=None, max_length=500)
    operator_id: str | None = None
