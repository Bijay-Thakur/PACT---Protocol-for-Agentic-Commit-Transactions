from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class BarrierCheck(BaseModel):
    code: str
    passed: bool
    subject: str | None = None
    detail: str = ""
    observed: dict[str, Any] = Field(default_factory=dict)
    blocking_reason: str | None = None


class CommitDecision(BaseModel):
    """Machine-readable output of the global commit barrier."""

    transaction_id: UUID
    eligible: bool
    evaluated_at: datetime
    binding: bool = Field(
        description="True when evaluated under the aggregate lock as part of a commit attempt"
    )
    snapshot_version: int
    checks: list[BarrierCheck]
    blocking_reasons: list[str]
    explanation: str | None = None
