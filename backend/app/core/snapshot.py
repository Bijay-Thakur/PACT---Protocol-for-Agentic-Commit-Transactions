"""Immutable, consistent view of one root transaction tree.

Engines (authority, conflicts, graph, invariants, commit barrier) are pure
functions over a :class:`TreeSnapshot`. The snapshot is loaded inside a single
database transaction - under the root aggregate lock when the decision is
binding - so every check sees the same state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.domain.enums import Application, EffectState, TransactionState
from app.domain.invariant import InvariantDefinition
from app.domain.resource_claim import ResourceClaim


@dataclass(frozen=True)
class TxNode:
    id: UUID
    root_id: UUID
    parent_id: UUID | None
    depth: int
    actor_id: str
    objective: str
    state: TransactionState
    required: bool
    capability_id: UUID | None
    meta: dict[str, Any]
    policy: dict[str, Any]
    version: int
    created_at: datetime


@dataclass(frozen=True)
class CapNode:
    id: UUID
    transaction_id: UUID
    parent_capability_id: UUID | None
    subject_id: str
    issuer: str
    allowed_effect_types: tuple[str, ...]
    allowed_resources: tuple[str, ...]
    amount_limit: Decimal | None
    cumulative_limit: Decimal | None
    expires_at: datetime | None
    delegation_depth: int
    meta: dict[str, Any]


@dataclass(frozen=True)
class EffectNode:
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
    claims: tuple[ResourceClaim, ...]
    depends_on: tuple[str, ...]
    prepare_evidence: dict[str, Any]
    verification_result: dict[str, Any] | None
    dispatched_at: datetime | None
    verified_at: datetime | None
    application: Application = Application.NOT_SENT
    postcondition: str = "UNDETERMINED"
    restoration: str = "NOT_REQUIRED"
    observed_amount: Decimal | None = None
    max_exposure: Decimal | None = None
    currency: str | None = None
    slot: str | None = None

    @property
    def customer_id(self) -> str | None:
        return self.payload.get("customer_id")

    def exposure(self) -> Decimal | None:
        """Amount this effect commits (or may commit) against authority and budgets.

        Applied effects count their *observed* amount (an applied wrong amount stays in
        exposure); unknown outcomes count a conservative bound; unsent live effects count
        the requested amount; confirmed non-application and released effects count nothing.
        """
        if self.amount is None:
            return None
        if self.application == Application.APPLIED:
            return self.observed_amount if self.observed_amount is not None else self.amount
        if self.application == Application.UNKNOWN:
            return max(self.amount, self.max_exposure or self.amount)
        if self.application == Application.NOT_APPLIED_CONFIRMED:
            return None
        if self.state in (EffectState.ABORTED, EffectState.FAILED, EffectState.COMPENSATED):
            return None
        return self.amount


@dataclass(frozen=True)
class InvariantNode:
    id: UUID
    transaction_id: UUID
    definition: InvariantDefinition


@dataclass(frozen=True)
class LogicalOpNode:
    id: UUID
    operation_key: str
    status: str
    owner_root_id: UUID | None
    owner_effect_id: UUID | None


@dataclass(frozen=True)
class ExternalClaim:
    """A claim held by an effect in a *different* active root transaction."""

    root_id: UUID
    effect_id: UUID
    operation_key: str
    claim: ResourceClaim


@dataclass
class TreeSnapshot:
    root: TxNode
    txs: dict[UUID, TxNode]
    caps: dict[UUID, CapNode]
    effects: list[EffectNode]
    invariants: list[InvariantNode]
    logical_ops: dict[UUID, LogicalOpNode]
    approvals: list[dict[str, Any]] = field(default_factory=list)
    external_claims: list[ExternalClaim] = field(default_factory=list)

    def children(self, tx_id: UUID) -> list[TxNode]:
        return sorted((t for t in self.txs.values() if t.parent_id == tx_id), key=lambda t: (t.created_at, str(t.id)))

    def subtree_ids(self, tx_id: UUID) -> set[UUID]:
        out, stack = set(), [tx_id]
        while stack:
            cur = stack.pop()
            out.add(cur)
            stack.extend(t.id for t in self.txs.values() if t.parent_id == cur)
        return out

    def effects_in(self, tx_ids: set[UUID]) -> list[EffectNode]:
        return [e for e in self.effects if e.transaction_id in tx_ids]

    def effect_by_key(self) -> dict[str, list[EffectNode]]:
        out: dict[str, list[EffectNode]] = {}
        for e in self.effects:
            out.setdefault(e.operation_key, []).append(e)
        return out

    def cap_of(self, tx_id: UUID) -> CapNode | None:
        tx = self.txs[tx_id]
        return self.caps.get(tx.capability_id) if tx.capability_id else None
