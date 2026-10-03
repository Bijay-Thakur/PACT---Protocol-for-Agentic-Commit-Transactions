"""Results returned across the adapter boundary.

The executor's :class:`DispatchResult` and the verifier's
:class:`VerificationResult` are deliberately distinct types: a provider's
response is never treated as proof of business state.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import DispatchOutcome, ProviderFinding, VerificationStatus


class PrepareResult(BaseModel):
    ok: bool
    reason: str = ""
    observations: dict[str, Any] = Field(default_factory=dict)


class DispatchResult(BaseModel):
    outcome: DispatchOutcome
    http_status: int | None = None
    provider_reference: str | None = None
    response: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: float | None = None


class VerificationResult(BaseModel):
    status: VerificationStatus
    external_reference: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class ReconciliationFinding(BaseModel):
    finding: ProviderFinding
    external_reference: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class CompensationOutcome(BaseModel):
    dispatch: DispatchResult
    verification: VerificationResult | None = None
