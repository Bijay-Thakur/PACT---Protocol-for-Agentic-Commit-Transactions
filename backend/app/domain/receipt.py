"""Transaction receipt protocol object and canonical hashing.

Canonicalization rules (receipt_version "pact.receipt/v1"):
1. Keys sorted lexicographically at every depth.
2. Compact separators (",", ":"), UTF-8, no ASCII escaping.
3. Decimals rendered as normalized fixed-point strings; datetimes as ISO-8601 UTC;
   UUIDs and enums as their string values.
4. The ``receipt_hash`` field is excluded from the hash input.
5. Digest is SHA-256 over the canonical bytes, hex encoded.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel

RECEIPT_VERSION = "pact.receipt/v1"
HASH_FIELD = "receipt_hash"


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        # Money-scale values render with exactly two places; finer values keep
        # their significant digits. Either way equal values render identically.
        if value.as_tuple().exponent >= -2:
            return format(value.quantize(Decimal("0.01")), "f")
        return format(value.normalize(), "f")
    if isinstance(value, float):
        return _normalize(Decimal(repr(value)))
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump(mode="python"))
    return value


def canonical_json(payload: dict[str, Any]) -> str:
    body = {k: v for k, v in payload.items() if k != HASH_FIELD}
    return json.dumps(_normalize(body), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def receipt_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def normalized(payload: dict[str, Any]) -> dict[str, Any]:
    return _normalize(payload)


class TransactionReceipt(BaseModel):
    """Documented shape of a receipt (the stored payload is the canonical dict)."""

    receipt_version: str
    transaction_id: str
    root_id: str
    parent_id: str | None
    objective: str
    initiator: str
    participants: list[str]
    created_at: str
    finalized_at: str
    final_state: str
    outcome_statement: str
    capability_summary: list[dict[str, Any]]
    children: list[dict[str, Any]]
    effects: list[dict[str, Any]]
    invariant_results: list[dict[str, Any]]
    commit_decision: dict[str, Any] | None
    resource_claims: list[dict[str, Any]]
    execution_results: list[dict[str, Any]]
    verification_results: list[dict[str, Any]]
    reconciliation_events: list[dict[str, Any]]
    compensation_results: list[dict[str, Any]]
    uncompensated_effects: list[dict[str, Any]]
    human_actions: list[dict[str, Any]]
    external_references: list[dict[str, Any]]
    event_count: int
    receipt_hash: str
