"""Builders for pure-engine unit tests (no database)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.core.snapshot import CapNode, EffectNode, InvariantNode, LogicalOpNode, TreeSnapshot, TxNode
from app.domain.enums import ClaimMode, EffectState, LogicalOperationStatus, TransactionState
from app.domain.invariant import InvariantDefinition
from app.domain.resource_claim import ResourceClaim

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def cap(subject: str, types: list[str], resources: list[str], amount: str | None = None,
        cumulative: str | None = None, depth: int = 0, parent: CapNode | None = None,
        expires_at: datetime | None = None, tx_id: uuid.UUID | None = None) -> CapNode:
    return CapNode(
        id=uuid.uuid4(), transaction_id=tx_id or uuid.uuid4(),
        parent_capability_id=parent.id if parent else None, subject_id=subject, issuer="operator:demo",
        allowed_effect_types=tuple(types), allowed_resources=tuple(resources),
        amount_limit=Decimal(amount) if amount is not None else None,
        cumulative_limit=Decimal(cumulative) if cumulative is not None else None,
        expires_at=expires_at, delegation_depth=depth, meta={},
    )


def effect(key: str, etype: str = "billing.refund", *, actor: str = "billing_agent", tx_id: uuid.UUID | None = None,
           amount: str | None = None, claims: list[tuple[str, str]] | None = None, deps: list[str] | None = None,
           state: EffectState = EffectState.PREPARED, payload: dict[str, Any] | None = None,
           evidence: dict | None = None, verification: dict | None = None,
           dispatched_at: datetime | None = None, verified_at: datetime | None = None,
           op_id: uuid.UUID | None = None) -> EffectNode:
    return EffectNode(
        id=uuid.uuid4(), transaction_id=tx_id or uuid.uuid4(), root_id=uuid.uuid4(),
        logical_operation_id=op_id or uuid.uuid4(), operation_key=key, effect_type=etype, actor_id=actor,
        state=state, payload=payload or {}, amount=Decimal(amount) if amount is not None else None,
        claims=tuple(ResourceClaim(resource=r, mode=ClaimMode(m)) for r, m in (claims or [])),
        depends_on=tuple(deps or []), prepare_evidence=evidence or {}, verification_result=verification,
        dispatched_at=dispatched_at, verified_at=verified_at,
    )


class Tree:
    """Mutable builder that produces a TreeSnapshot."""

    def __init__(self, root_cap_kw: dict | None = None, meta: dict | None = None):
        self.root_id = uuid.uuid4()
        kw = {"types": ["billing.refund", "subscription.cancel", "notification.send", "identity.revoke",
                        "identity.grant", "crm.update"],
              "resources": ["customer:C1/*"], "amount": "500", "cumulative": "500", "depth": 2}
        kw.update(root_cap_kw or {})
        self.root_cap = cap("root_agent", kw["types"], kw["resources"], kw["amount"], kw["cumulative"],
                            kw["depth"], tx_id=self.root_id)
        self.txs = {self.root_id: TxNode(
            id=self.root_id, root_id=self.root_id, parent_id=None, depth=0, actor_id="root_agent", objective="t",
            state=TransactionState.PREPARED, required=True, capability_id=self.root_cap.id,
            meta=meta or {"customer_id": "C1"}, policy={}, version=3, created_at=NOW)}
        self.caps = {self.root_cap.id: self.root_cap}
        self.effects: list[EffectNode] = []
        self.invariants: list[InvariantNode] = []
        self.ops: dict[uuid.UUID, LogicalOpNode] = {}
        self._n = 0

    def child(self, actor: str, types: list[str], resources: list[str], amount: str = "0", cumulative: str | None = None,
              state: TransactionState = TransactionState.PREPARED, required: bool = True) -> uuid.UUID:
        self._n += 1
        tx_id = uuid.uuid4()
        c = cap(actor, types, resources, amount, cumulative if cumulative is not None else amount, 0,
                parent=self.root_cap, tx_id=tx_id)
        self.caps[c.id] = c
        self.txs[tx_id] = TxNode(id=tx_id, root_id=self.root_id, parent_id=self.root_id, depth=1, actor_id=actor,
                                 objective=actor, state=state, required=required, capability_id=c.id, meta={},
                                 policy={}, version=1, created_at=NOW + timedelta(seconds=self._n))
        return tx_id

    def add(self, tx_id: uuid.UUID, key: str, etype: str, *, op_status: str = LogicalOperationStatus.AVAILABLE,
            op_owner: uuid.UUID | None = None, **kw) -> EffectNode:
        actor = self.txs[tx_id].actor_id
        op_id = uuid.uuid4()
        e = effect(key, etype, actor=actor, tx_id=tx_id, op_id=op_id, **kw)
        self.effects.append(e)
        self.ops[op_id] = LogicalOpNode(id=op_id, operation_key=key, status=str(op_status),
                                        owner_root_id=op_owner, owner_effect_id=None)
        return e

    def invariant(self, **kw) -> None:
        self.invariants.append(InvariantNode(id=uuid.uuid4(), transaction_id=self.root_id,
                                             definition=InvariantDefinition(**kw)))

    def snapshot(self) -> TreeSnapshot:
        return TreeSnapshot(root=self.txs[self.root_id], txs=self.txs, caps=self.caps, effects=self.effects,
                            invariants=self.invariants, logical_ops=self.ops)
