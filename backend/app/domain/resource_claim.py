from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, field_validator

from app.domain.enums import ClaimMode

# Logical resource identifiers, e.g. "customer:C-48291/refund_balance".
RESOURCE_RE = re.compile(r"^[A-Za-z0-9_.:\-/@]+$")
# Authority patterns may additionally end in a single trailing "*".
RESOURCE_PATTERN_RE = re.compile(r"^(\*|[A-Za-z0-9_.:\-/@]+\*?)$")

_MODE_RANK = {ClaimMode.READ: 0, ClaimMode.WRITE: 1, ClaimMode.EXCLUSIVE: 2}


class ResourceClaim(BaseModel):
    model_config = ConfigDict(frozen=True)

    resource: str
    mode: ClaimMode

    @field_validator("resource")
    @classmethod
    def _valid_resource(cls, v: str) -> str:
        if not RESOURCE_RE.match(v) or len(v) > 256:
            raise ValueError(f"invalid resource identifier: {v!r}")
        return v


def merge_claims(*groups: list[ResourceClaim]) -> list[ResourceClaim]:
    """Merge claim lists, keeping the strongest mode per resource (sorted, deterministic)."""
    strongest: dict[str, ClaimMode] = {}
    for group in groups:
        for claim in group:
            cur = strongest.get(claim.resource)
            if cur is None or _MODE_RANK[claim.mode] > _MODE_RANK[cur]:
                strongest[claim.resource] = claim.mode
    return [ResourceClaim(resource=r, mode=m) for r, m in sorted(strongest.items())]
