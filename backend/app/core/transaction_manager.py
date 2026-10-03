"""Transaction manager.

Creates root and child transactions, delegates capabilities, attaches effect
proposals and invariants, and owns every state mutation: all transitions go
through :meth:`transition_tx` / :meth:`transition_effect`, which validate against
the central state machine and append an event in the same database transaction.

It is the top-level bookkeeper - not the policy engine and not the executor.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Callable
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import provider_idempotency_key
from app.adapters.registry import EffectRegistry
from app.core.authority_engine import AuthorityEngine
from app.core.state_machine import (
    TERMINAL_TX_STATES,
    assert_effect_transition,
    assert_tx_transition,
)
from app.domain.capability import CapabilitySpec, RootCapabilityGrant
from app.domain.effect import EffectProposal
from app.domain.enums import (
    EffectState,
    EventType,
    LogicalOperationStatus,
    OperatorActionType,
    TransactionState,
)
from app.domain.errors import AuthorityViolation, StateConflict, ValidationFailed
from app.domain.invariant import InvariantDefinition
from app.domain.resource_claim import merge_claims
from app.domain.transaction import ChildSpec, TransactionSpec
from app.persistence.db import Database
from app.persistence.models import (
    CapabilityRow,
    EffectRow,
    InvariantRow,
    LogicalOperationRow,
    OperatorActionRow,
    TransactionRow,
)
from app.persistence.repositories import append_event, get_tx, load_tree, lock_root
from app.telemetry.tracing import span

Clock = Callable[[], datetime]

SPECIFIABLE = {TransactionState.CREATED, TransactionState.SPECIFYING}


def utcnow() -> datetime:
    return datetime.now(UTC)


class TransactionManager:
    def __init__(self, db: Database, registry: EffectRegistry, authority: AuthorityEngine, clock: Clock = utcnow):
        self.db = db
        self.registry = registry
        self.authority = authority
        self.clock = clock

    # ------------------------------------------------------------------ states
    def transition_tx(
        self, s: AsyncSession, tx: TransactionRow, to: TransactionState, reason: str,
        *, actor: str = "pact", **payload: Any,
    ) -> None:
        frm = tx.state
        assert_tx_transition(frm, to)
        tx.state = str(to)
        if to in TERMINAL_TX_STATES:
            tx.finalized_at = self.clock()
        append_event(s, tx, EventType.TRANSACTION_STATE_CHANGED,
                     {"from": frm, "to": str(to), "reason": reason, **payload}, actor=actor)

    def transition_effect(
        self, s: AsyncSession, tx: TransactionRow, effect: EffectRow, to: EffectState, reason: str,
        *, event_type: EventType = EventType.EFFECT_STATE_CHANGED, actor: str = "pact", **payload: Any,
    ) -> None:
        frm = effect.state
        assert_effect_transition(frm, to)
        effect.state = str(to)
        append_event(s, tx, event_type,
                     {"from": frm, "to": str(to), "reason": reason, "operation_key": effect.operation_key,
                      "effect_type": effect.contract_type, **payload},
                     effect_id=effect.id, actor=actor)

    def ensure_specifying(self, s: AsyncSession, tx: TransactionRow, reason: str) -> None:
        if tx.state == TransactionState.CREATED:
            self.transition_tx(s, tx, TransactionState.SPECIFYING, reason)
        elif tx.state != TransactionState.SPECIFYING:
            raise StateConflict(f"transaction is {tx.state}; specification is closed",
                                code="SPECIFICATION_CLOSED", details={"state": tx.state})

    # ---------------------------------------------------------------- creation
    async def create_root(self, spec: TransactionSpec) -> UUID:
        now = self.clock()
        violations = self.authority.validate_root_grant(
            spec.capability, {c.effect_type for c in self.registry.contracts()}, now)
        if violations:
            raise AuthorityViolation("root capability grant rejected", code="ROOT_GRANT_REJECTED",
                                     details=[v.model_dump() for v in violations])
        keys = [i.key for i in spec.invariants]
        if len(keys) != len(set(keys)):
            raise ValidationFailed("invariant keys must be unique", code="DUPLICATE_INVARIANT_KEY")
        with span("transaction.create", actor_id=spec.actor_id) as sp:
            async with self.db.uow() as s:
                tx_id = uuid.uuid4()
                cap = self._capability_row(tx_id, None, spec.actor_id, spec.capability.issuer, spec.capability)
                meta = {
                    **spec.metadata,
                    "participants": spec.participants,
                    "required_effect_types": sorted(spec.required_effect_types),
                    "requires_approval": spec.requires_approval,
                }
                tx = TransactionRow(
                    id=tx_id, root_id=tx_id, parent_id=None, depth=0, objective=spec.objective,
                    actor_id=spec.actor_id, capability_id=cap.id, state=str(TransactionState.CREATED),
                    required=True, meta=meta, policy=spec.recovery_policy.model_dump(),
                )
                s.add(tx)
                await s.flush()
                s.add(cap)
                await s.flush()
                append_event(s, tx, EventType.TRANSACTION_CREATED,
                             {"objective": spec.objective, "actor_id": spec.actor_id, "metadata": meta,
                              "recovery_policy": spec.recovery_policy.model_dump()}, actor=spec.actor_id)
                append_event(s, tx, EventType.CAPABILITY_ISSUED,
                             {"capability_id": str(cap.id), "issuer": cap.issuer, "subject": cap.subject_id,
                              **self._cap_summary(cap)}, actor=spec.capability.issuer)
                for inv in spec.invariants:
                    await self._add_invariant(s, tx, inv)
                for p in spec.effects:
                    await self._propose(s, tx, tx, p)
                for child in spec.children:
                    child_tx = await self._create_child(s, tx, tx, child)
                    for p in child.effects:
                        await self._propose(s, tx, child_tx, p)
            sp.set_attribute("pact.transaction_id", str(tx_id))
        return tx_id

    async def create_child(self, parent_id: UUID, child: ChildSpec) -> UUID:
        rejection: AuthorityViolation | None = None
        child_id: UUID | None = None
        async with self.db.uow() as s:
            parent = await get_tx(s, parent_id)
            root = await lock_root(s, parent.root_id)
            await s.refresh(parent)
            try:
                child_tx = await self._create_child(s, root, parent, child)
            except AuthorityViolation as exc:
                # Raised before any write, so this unit of work commits nothing.
                rejection = exc
            else:
                for p in child.effects:
                    await self._propose(s, root, child_tx, p)
                child_id = child_tx.id
        if rejection is not None:
            # Persist the rejection for auditability in its own unit of work.
            async with self.db.uow() as s:
                parent = await get_tx(s, parent_id)
                append_event(s, parent, EventType.CAPABILITY_DELEGATION_REJECTED,
                             {"child_actor_id": child.actor_id, "violations": rejection.details}, actor=parent.actor_id)
            raise rejection
        assert child_id is not None
        return child_id

    async def _create_child(self, s: AsyncSession, root: TransactionRow, parent: TransactionRow, child: ChildSpec) -> TransactionRow:
        if root.state in TERMINAL_TX_STATES:
            raise StateConflict("root transaction is finalized", code="TRANSACTION_FINALIZED")
        parent_cap = await s.get(CapabilityRow, parent.capability_id) if parent.capability_id else None
        if parent_cap is None:
            raise AuthorityViolation("parent has no capability to delegate", code="NO_CAPABILITY")
        from app.persistence.repositories import cap_node

        violations = self.authority.validate_delegation(cap_node(parent_cap), child.capability, self.clock())
        if violations:
            raise AuthorityViolation(
                f"delegation from {parent.actor_id} to {child.actor_id} rejected: "
                + ", ".join(sorted({v.code for v in violations})),
                code="DELEGATION_REJECTED", details=[v.model_dump() for v in violations])
        self.ensure_specifying(s, parent, f"child transaction delegated to {child.actor_id}")
        child_id = uuid.uuid4()
        cap = self._capability_row(child_id, parent_cap.id, child.actor_id, f"delegation:{parent.actor_id}", child.capability)
        tx = TransactionRow(
            id=child_id, root_id=root.id, parent_id=parent.id, depth=parent.depth + 1, objective=child.objective,
            actor_id=child.actor_id, capability_id=cap.id, state=str(TransactionState.CREATED),
            required=child.required, meta=child.metadata, policy={},
        )
        s.add(tx)
        await s.flush()
        s.add(cap)
        await s.flush()
        append_event(s, parent, EventType.CHILD_TRANSACTION_CREATED,
                     {"child_id": str(child_id), "actor_id": child.actor_id, "objective": child.objective,
                      "required": child.required}, actor=parent.actor_id)
        append_event(s, tx, EventType.CAPABILITY_DELEGATED,
                     {"capability_id": str(cap.id), "parent_capability_id": str(parent_cap.id),
                      "delegator": parent.actor_id, "subject": child.actor_id, **self._cap_summary(cap)},
                     actor=parent.actor_id)
        for inv in child.invariants:
            await self._add_invariant(s, tx, inv)
        return tx

    def _capability_row(self, tx_id: UUID, parent_cap_id: UUID | None, subject: str, issuer: str,
                        spec: CapabilitySpec | RootCapabilityGrant) -> CapabilityRow:
        return CapabilityRow(
            id=uuid.uuid4(), transaction_id=tx_id, parent_capability_id=parent_cap_id, subject_id=subject,
            issuer=issuer,
            scope={"allowed_effect_types": spec.allowed_effect_types, "allowed_resources": spec.allowed_resources},
            amount_limit=spec.amount_limit, cumulative_limit=spec.cumulative_amount_limit,
            expires_at=spec.expires_at, delegation_depth=spec.delegation_depth, meta=spec.metadata,
        )

    @staticmethod
    def _cap_summary(cap: CapabilityRow) -> dict[str, Any]:
        return {
            "allowed_effect_types": cap.scope["allowed_effect_types"],
            "allowed_resources": cap.scope["allowed_resources"],
            "amount_limit": str(cap.amount_limit) if cap.amount_limit is not None else None,
            "cumulative_limit": str(cap.cumulative_limit) if cap.cumulative_limit is not None else None,
            "delegation_depth": cap.delegation_depth,
            "expires_at": cap.expires_at.isoformat() if cap.expires_at else None,
        }

    # -------------------------------------------------------------- invariants
    async def add_invariant(self, tx_id: UUID, inv: InvariantDefinition) -> None:
        async with self.db.uow() as s:
            tx = await get_tx(s, tx_id)
            await lock_root(s, tx.root_id)
            await s.refresh(tx)
            self.ensure_specifying(s, tx, f"invariant {inv.key} registered")
            await self._add_invariant(s, tx, inv)

    async def _add_invariant(self, s: AsyncSession, tx: TransactionRow, inv: InvariantDefinition) -> None:
        exists = (await s.execute(select(InvariantRow.id).where(
            InvariantRow.transaction_id == tx.id, InvariantRow.key == inv.key))).first()
        if exists:
            raise ValidationFailed(f"invariant {inv.key} already registered", code="DUPLICATE_INVARIANT_KEY")
        s.add(InvariantRow(transaction_id=tx.id, key=inv.key, name=inv.name, phase=str(inv.phase),
                           expression_type=inv.expression_type, definition=inv.config,
                           severity=str(inv.severity), failure_action=str(inv.failure_action),
                           description=inv.description))
        append_event(s, tx, EventType.INVARIANT_REGISTERED, inv.model_dump(mode="json"))

    # ---------------------------------------------------------------- proposals
    async def propose_effect(self, tx_id: UUID, proposal: EffectProposal) -> UUID:
        async with self.db.uow() as s:
            tx = await get_tx(s, tx_id)
            root = await lock_root(s, tx.root_id)
            await s.refresh(tx)
            eff = await self._propose(s, root, tx, proposal)
            return eff.id

    async def _propose(self, s: AsyncSession, root: TransactionRow, tx: TransactionRow, p: EffectProposal) -> EffectRow:
        with span("effect.propose", transaction_id=tx.id, root_id=root.id, actor_id=p.actor_id,
                  effect_type=p.effect_type, operation_key=p.operation_key):
            if p.actor_id != tx.actor_id:
                raise AuthorityViolation(
                    f"actor {p.actor_id!r} is not bound to transaction {tx.id} (bound actor: {tx.actor_id!r})",
                    code="ACTOR_NOT_BOUND_TO_TRANSACTION")
            contract = self.registry.contract(p.effect_type)
            try:
                payload_model = contract.payload_model(**p.payload)
            except ValidationError as exc:
                raise ValidationFailed(f"payload invalid for {p.effect_type}", code="EFFECT_PAYLOAD_INVALID",
                                       details=json.loads(exc.json(include_url=False))) from None
            payload = payload_model.model_dump(mode="json")
            amount = Decimal(str(payload[contract.amount_field])) if contract.amount_field else None
            required = contract.required_claims(payload) if contract.required_claims else []
            claims = merge_claims(list(p.resource_claims), required)
            self.ensure_specifying(s, tx, f"effect {p.operation_key} proposed")

            lop = await self._logical_operation(s, p.operation_key, p.effect_type)
            effect = EffectRow(
                id=uuid.uuid4(), transaction_id=tx.id, root_id=root.id, logical_operation_id=lop.id,
                operation_key=p.operation_key, contract_type=p.effect_type, actor_id=p.actor_id,
                state=str(EffectState.PROPOSED), payload=payload, amount=amount,
                resource_claims=[c.model_dump(mode="json") for c in claims], depends_on=sorted(set(p.depends_on)),
                reversibility_class=str(contract.reversibility_class),
                provider_idempotency_key=lop.provider_idempotency_key, prepare_evidence={},
            )
            s.add(effect)
            await s.flush()
            append_event(s, tx, EventType.EFFECT_PROPOSED, {
                "operation_key": p.operation_key, "effect_type": p.effect_type, "actor_id": p.actor_id,
                "payload": payload, "resource_claims": effect.resource_claims, "depends_on": effect.depends_on,
                "declared_claims": [c.model_dump(mode="json") for c in p.resource_claims],
            }, effect_id=effect.id, actor=p.actor_id)
            self.transition_effect(s, tx, effect, EffectState.VALIDATED,
                                   "schema valid and bound to registered contract",
                                   event_type=EventType.EFFECT_VALIDATED,
                                   contract=contract.public())
            dupes = (await s.execute(select(EffectRow.id).where(
                EffectRow.root_id == root.id, EffectRow.operation_key == p.operation_key,
                EffectRow.id != effect.id))).first()
            if dupes:
                append_event(s, tx, EventType.CONFLICT_DETECTED, {
                    "code": "DUPLICATE_OPERATION_KEY", "operation_key": p.operation_key,
                    "detail": "same logical operation proposed twice in this transaction; the commit barrier will block",
                }, effect_id=effect.id)
            return effect

    async def _logical_operation(self, s: AsyncSession, key: str, effect_type: str) -> LogicalOperationRow:
        """Upsert the logical operation for ``key`` (UNIQUE(operation_key) arbitrates races)."""
        await s.execute(
            pg_insert(LogicalOperationRow)
            .values(id=uuid.uuid4(), operation_key=key, effect_type=effect_type,
                    status=str(LogicalOperationStatus.AVAILABLE),
                    provider_idempotency_key=provider_idempotency_key(key), version=1)
            .on_conflict_do_nothing(index_elements=["operation_key"])
        )
        lop = (await s.execute(select(LogicalOperationRow).where(LogicalOperationRow.operation_key == key))).scalar_one()
        if lop.effect_type != effect_type:
            raise ValidationFailed(
                f"operation_key {key!r} already identifies a {lop.effect_type} operation",
                code="OPERATION_KEY_TYPE_MISMATCH")
        return lop

    # --------------------------------------------------------- operator actions
    async def record_operator_action(
        self, s: AsyncSession, tx: TransactionRow, operator_id: str, action: OperatorActionType,
        note: str, effect_id: UUID | None = None, payload: dict | None = None,
    ) -> None:
        s.add(OperatorActionRow(transaction_id=tx.id, effect_id=effect_id, operator_id=operator_id,
                                action=str(action), note=note, payload=payload or {}))
        append_event(s, tx, EventType.OPERATOR_ACTION_RECORDED,
                     {"operator_id": operator_id, "action": str(action), "note": note,
                      "effect_id": str(effect_id) if effect_id else None, **(payload or {})},
                     effect_id=effect_id, actor=f"operator:{operator_id}")

    async def approve(self, root_id: UUID, operator_id: str, note: str) -> None:
        async with self.db.uow() as s:
            rows = await load_tree(s, root_id, lock=True)
            if rows.root.state not in (*SPECIFIABLE, TransactionState.PREPARING, TransactionState.PREPARED):
                raise StateConflict("approvals are only accepted before commit", code="APPROVAL_TOO_LATE")
            await self.record_operator_action(s, rows.root, operator_id, OperatorActionType.APPROVE, note)
