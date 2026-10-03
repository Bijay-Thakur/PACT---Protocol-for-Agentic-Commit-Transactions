"""Loading and locking of transaction aggregates.

Locking protocol (prevents deadlocks and gives the barrier a consistent snapshot):
1. Every mutating unit of work first takes ``SELECT ... FOR UPDATE`` on the
   *root* transaction row - the aggregate lock for the whole tree.
2. Binding barrier evaluations additionally lock the tree's logical-operation
   rows in ``operation_key`` order, because logical operations are shared across
   roots (that is where cross-transaction duplicates race).
3. Rows carry ``version`` columns, so any write that bypassed the lock would
   still fail the optimistic check instead of silently overwriting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.snapshot import (
    CapNode,
    EffectNode,
    ExternalClaim,
    InvariantNode,
    LogicalOpNode,
    TreeSnapshot,
    TxNode,
)
from app.core.state_machine import EXPOSURE_EFFECT_STATES, POST_BARRIER_TX_STATES
from app.domain.effect import EffectView
from app.domain.enums import EffectState, OperatorActionType, ReversibilityClass, TransactionState
from app.domain.errors import NotFound
from app.domain.invariant import InvariantDefinition
from app.domain.resource_claim import ResourceClaim
from app.persistence.models import (
    CapabilityRow,
    EffectRow,
    InvariantRow,
    LogicalOperationRow,
    OperatorActionRow,
    TransactionEventRow,
    TransactionRow,
)


async def get_tx(s: AsyncSession, tx_id: UUID) -> TransactionRow:
    tx = await s.get(TransactionRow, tx_id)
    if tx is None:
        raise NotFound(f"transaction {tx_id} not found", code="TRANSACTION_NOT_FOUND")
    return tx


async def lock_root(s: AsyncSession, root_id: UUID) -> TransactionRow:
    row = (await s.execute(
        select(TransactionRow).where(TransactionRow.id == root_id).with_for_update()
    )).scalar_one_or_none()
    if row is None:
        raise NotFound(f"transaction {root_id} not found", code="TRANSACTION_NOT_FOUND")
    return row


def append_event(
    s: AsyncSession, tx: TransactionRow, event_type: str, payload: dict[str, Any] | None = None,
    *, effect_id: UUID | None = None, actor: str = "pact",
) -> TransactionEventRow:
    ev = TransactionEventRow(
        transaction_id=tx.id, root_id=tx.root_id, effect_id=effect_id,
        event_type=str(event_type), actor=actor, payload=payload or {},
    )
    s.add(ev)
    return ev


@dataclass
class TreeRows:
    root: TransactionRow
    txs: dict[UUID, TransactionRow]
    caps: dict[UUID, CapabilityRow]
    effects: dict[UUID, EffectRow]
    invariants: list[InvariantRow]
    logical_ops: dict[UUID, LogicalOperationRow]
    approvals: list[OperatorActionRow]
    external_claims: list[ExternalClaim]

    def ordered_txs(self) -> list[TransactionRow]:
        return sorted(self.txs.values(), key=lambda t: (t.depth, t.created_at, str(t.id)))

    def effects_of(self, tx_id: UUID) -> list[EffectRow]:
        return sorted((e for e in self.effects.values() if e.transaction_id == tx_id), key=lambda e: e.operation_key)

    def snapshot(self) -> TreeSnapshot:
        return TreeSnapshot(
            root=tx_node(self.root),
            txs={t.id: tx_node(t) for t in self.txs.values()},
            caps={c.id: cap_node(c) for c in self.caps.values()},
            effects=[effect_node(e) for e in sorted(self.effects.values(), key=lambda e: (e.operation_key, str(e.id)))],
            invariants=[InvariantNode(id=i.id, transaction_id=i.transaction_id,
                                      definition=invariant_definition(i)) for i in self.invariants],
            logical_ops={o.id: LogicalOpNode(id=o.id, operation_key=o.operation_key, status=o.status,
                                             owner_root_id=o.owner_root_id, owner_effect_id=o.owner_effect_id)
                         for o in self.logical_ops.values()},
            approvals=[{"operator_id": a.operator_id, "note": a.note, "at": a.created_at.isoformat()}
                       for a in self.approvals],
            external_claims=self.external_claims,
        )


async def load_tree(s: AsyncSession, root_id: UUID, *, lock: bool, lock_operations: bool = False) -> TreeRows:
    root = await lock_root(s, root_id) if lock else await get_tx(s, root_id)
    if root.parent_id is not None:
        raise NotFound(f"{root_id} is not a root transaction", code="NOT_A_ROOT_TRANSACTION")
    txs_stmt = select(TransactionRow).where(TransactionRow.root_id == root_id).order_by(TransactionRow.id)
    if lock:
        txs_stmt = txs_stmt.with_for_update()
    txs = {t.id: t for t in (await s.execute(txs_stmt)).scalars()}
    ids = list(txs)
    caps = {c.id: c for c in (await s.execute(select(CapabilityRow).where(CapabilityRow.transaction_id.in_(ids)))).scalars()}
    eff_stmt = select(EffectRow).where(EffectRow.root_id == root_id).order_by(EffectRow.id)
    if lock:
        eff_stmt = eff_stmt.with_for_update()
    effects = {e.id: e for e in (await s.execute(eff_stmt)).scalars()}
    invariants = list((await s.execute(select(InvariantRow).where(InvariantRow.transaction_id.in_(ids)))).scalars())
    op_ids = sorted({e.logical_operation_id for e in effects.values()})
    lop_stmt = select(LogicalOperationRow).where(LogicalOperationRow.id.in_(op_ids)).order_by(LogicalOperationRow.operation_key)
    if lock_operations:
        lop_stmt = lop_stmt.with_for_update()
    lops = {o.id: o for o in (await s.execute(lop_stmt)).scalars()} if op_ids else {}
    approvals = list((await s.execute(
        select(OperatorActionRow).where(OperatorActionRow.transaction_id == root_id,
                                        OperatorActionRow.action == OperatorActionType.APPROVE)
        .order_by(OperatorActionRow.created_at)
    )).scalars())
    external = await _external_claims(s, root_id, effects)
    return TreeRows(root=root, txs=txs, caps=caps, effects=effects, invariants=invariants,
                    logical_ops=lops, approvals=approvals, external_claims=external)


async def _external_claims(s: AsyncSession, root_id: UUID, effects: dict[UUID, EffectRow]) -> list[ExternalClaim]:
    resources = {c["resource"] for e in effects.values() for c in e.resource_claims}
    if not resources:
        return []
    stmt = (
        select(EffectRow)
        .join(TransactionRow, TransactionRow.id == EffectRow.root_id)
        .where(EffectRow.root_id != root_id,
               TransactionRow.state.in_([str(x) for x in POST_BARRIER_TX_STATES]),
               EffectRow.state.in_([str(x) for x in EXPOSURE_EFFECT_STATES]))
    )
    out = []
    for e in (await s.execute(stmt)).scalars():
        for c in e.resource_claims:
            if c["resource"] in resources:
                out.append(ExternalClaim(root_id=e.root_id, effect_id=e.id, operation_key=e.operation_key,
                                         claim=ResourceClaim(**c)))
    return out


async def next_attempt_no(s: AsyncSession, logical_operation_id: UUID, kind: str) -> int:
    from app.persistence.models import OperationAttemptRow

    n = (await s.execute(
        select(func.count()).select_from(OperationAttemptRow)
        .where(OperationAttemptRow.logical_operation_id == logical_operation_id, OperationAttemptRow.kind == kind)
    )).scalar_one()
    return int(n) + 1


# -- row -> immutable node conversion -------------------------------------

def tx_node(t: TransactionRow) -> TxNode:
    return TxNode(id=t.id, root_id=t.root_id, parent_id=t.parent_id, depth=t.depth, actor_id=t.actor_id,
                  objective=t.objective, state=TransactionState(t.state), required=t.required,
                  capability_id=t.capability_id, meta=dict(t.meta or {}), policy=dict(t.policy or {}),
                  version=t.version, created_at=t.created_at)


def cap_node(c: CapabilityRow) -> CapNode:
    return CapNode(id=c.id, transaction_id=c.transaction_id, parent_capability_id=c.parent_capability_id,
                   subject_id=c.subject_id, issuer=c.issuer,
                   allowed_effect_types=tuple(c.scope.get("allowed_effect_types", [])),
                   allowed_resources=tuple(c.scope.get("allowed_resources", [])),
                   amount_limit=_d(c.amount_limit), cumulative_limit=_d(c.cumulative_limit),
                   expires_at=c.expires_at, delegation_depth=c.delegation_depth, meta=dict(c.meta or {}))


def effect_node(e: EffectRow) -> EffectNode:
    return EffectNode(id=e.id, transaction_id=e.transaction_id, root_id=e.root_id,
                      logical_operation_id=e.logical_operation_id, operation_key=e.operation_key,
                      effect_type=e.contract_type, actor_id=e.actor_id, state=EffectState(e.state),
                      payload=dict(e.payload), amount=_d(e.amount),
                      claims=tuple(ResourceClaim(**c) for c in e.resource_claims),
                      depends_on=tuple(e.depends_on or []), prepare_evidence=dict(e.prepare_evidence or {}),
                      verification_result=e.verification_result, dispatched_at=e.dispatched_at,
                      verified_at=e.verified_at)


def effect_view(e: EffectRow) -> EffectView:
    return EffectView(
        id=e.id, transaction_id=e.transaction_id, root_id=e.root_id, logical_operation_id=e.logical_operation_id,
        operation_key=e.operation_key, effect_type=e.contract_type, actor_id=e.actor_id,
        state=EffectState(e.state), payload=dict(e.payload), amount=_d(e.amount),
        resource_claims=[ResourceClaim(**c) for c in e.resource_claims], depends_on=list(e.depends_on or []),
        reversibility_class=ReversibilityClass(e.reversibility_class),
        provider_idempotency_key=e.provider_idempotency_key, provider_reference=e.provider_reference,
        prepare_evidence=dict(e.prepare_evidence or {}), dispatch_result=e.dispatch_result,
        verification_result=e.verification_result, reconciliation_result=e.reconciliation_result,
        compensation_result=e.compensation_result, created_at=e.created_at, updated_at=e.updated_at,
        dispatched_at=e.dispatched_at, verified_at=e.verified_at,
    )


def invariant_definition(i: InvariantRow) -> InvariantDefinition:
    return InvariantDefinition(key=i.key, name=i.name, phase=i.phase, expression_type=i.expression_type,
                               config=i.definition, severity=i.severity, failure_action=i.failure_action,
                               description=i.description)


def _d(v: Any) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None
