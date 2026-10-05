"""Results returned across the adapter boundary.

The executor's :class:`DispatchResult` (what the provider *said*) and an
:class:`Observation` (what the provider's state *shows*) are deliberately
distinct types: a provider's response is never treated as proof of business state.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import (
    Application,
    Consistency,
    DispatchOutcome,
    Postcondition,
    ProviderFinding,
    Restoration,
    VerificationStatus,
)


class PrepareResult(BaseModel):
    ok: bool
    reason: str = ""
    observations: dict[str, Any] = Field(default_factory=dict)
    # Values resolved by trusted preparation that become part of the frozen
    # execution payload (e.g. the specific charge to refund).
    resolved: dict[str, Any] = Field(default_factory=dict)
    # Material provider preconditions re-checked for freshness before the barrier.
    preconditions: dict[str, Any] = Field(default_factory=dict)
    # Conservative upper bound of what the provider could do (budget holds).
    max_exposure: Decimal | None = None
    provenance: str = "SIMULATED"


class DispatchResult(BaseModel):
    outcome: DispatchOutcome
    http_status: int | None = None
    provider_reference: str | None = None
    response: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: float | None = None
    stale_precondition: bool = False


class Observation(BaseModel):
    """Normalized external evidence about one effect.

    ``application`` (did it take effect?) and ``postcondition`` (does the external
    state match what was declared?) are independent. ``absent`` means the target
    record was not found; whether that is authoritative negative evidence is
    decided by the contract's negative-evidence rule, not by the adapter alone.
    """

    application: Application
    postcondition: Postcondition
    restoration: Restoration | None = None
    consistency: Consistency = Consistency.STRONG
    pending: bool = False
    absent: bool = False
    observed_amount: Decimal | None = None
    provider_reference: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    source: str = ""
    readable: bool = True
    provenance: str = "SIMULATED"

    @property
    def verification_status(self) -> VerificationStatus:
        # The obligation is satisfied when the declared postcondition holds. Causality
        # (did *this* operation cause it?) is recorded separately in ``application``.
        if not self.readable:
            return VerificationStatus.VERIFICATION_UNKNOWN
        if self.postcondition == Postcondition.MATCH:
            return VerificationStatus.VERIFIED_SUCCESS
        if self.pending:
            return VerificationStatus.VERIFICATION_PENDING
        if self.application in (Application.APPLIED, Application.NOT_APPLIED_CONFIRMED):
            return VerificationStatus.VERIFIED_FAILURE
        return VerificationStatus.VERIFICATION_UNKNOWN


# --- kept for compatibility with Phase 1 call sites / receipts -------------

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
