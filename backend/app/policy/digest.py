"""Canonical plan digests and payload fingerprints.

Canonicalization ("pact.plan/v1"): the same rules as receipts - keys sorted at
every depth, compact separators, decimals as normalized fixed-point strings,
datetimes ISO-8601 UTC, UUIDs/enums as strings - then SHA-256 hex.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.domain.receipt import canonical_json

PLAN_CANONICALIZATION = "pact.plan/v1"


def digest(obj: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json({"canonicalization": PLAN_CANONICALIZATION, **obj}).encode()).hexdigest()


def fingerprint(payload: dict[str, Any]) -> str:
    """Immutable fingerprint of a resolved execution payload."""
    return hashlib.sha256(canonical_json({"payload": payload}).encode()).hexdigest()
