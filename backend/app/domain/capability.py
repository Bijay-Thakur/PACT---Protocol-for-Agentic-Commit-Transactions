from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.domain.resource_claim import RESOURCE_PATTERN_RE


class CapabilitySpec(BaseModel):
    """Requested authority envelope (root issuance or child delegation)."""

    allowed_effect_types: list[str] = Field(min_length=1)
    allowed_resources: list[str] = Field(min_length=1)
    amount_limit: Decimal | None = Field(default=None, ge=0)
    cumulative_amount_limit: Decimal | None = Field(default=None, ge=0)
    expires_at: datetime | None = None
    delegation_depth: int = Field(default=0, ge=0, le=8)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("allowed_resources")
    @classmethod
    def _patterns(cls, v: list[str]) -> list[str]:
        for p in v:
            if not RESOURCE_PATTERN_RE.match(p):
                raise ValueError(
                    f"invalid resource pattern {p!r}: only a single trailing '*' wildcard is supported"
                )
        return sorted(set(v))

    @field_validator("allowed_effect_types")
    @classmethod
    def _types(cls, v: list[str]) -> list[str]:
        return sorted(set(v))


class RootCapabilityGrant(CapabilitySpec):
    """A root capability must name the issuer that grants it.

    The issuer is checked server-side against configured issuer policies; an agent
    cannot mint its own authority by simply asserting a capability.
    """

    issuer: str = Field(min_length=1, max_length=128)


class CapabilityView(BaseModel):
    id: UUID
    transaction_id: UUID
    parent_capability_id: UUID | None
    subject_id: str
    issuer: str
    allowed_effect_types: list[str]
    allowed_resources: list[str]
    amount_limit: Decimal | None
    cumulative_amount_limit: Decimal | None
    expires_at: datetime | None
    delegation_depth: int
    metadata: dict[str, Any]
    created_at: datetime


class AuthorityViolationItem(BaseModel):
    code: str
    detail: str
    observed: dict[str, Any] = Field(default_factory=dict)
