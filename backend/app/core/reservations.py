"""Durable resource reservations and budget ledger.

Resources are hierarchical canonical keys (``billing/customer:C-1/refunds/charge:ch_9``),
materialized as rows. A claim locks its own row and every ancestor row from depth 2
(``billing/customer:C-1`` ...) in global key order, inside the binding barrier
transaction. Two claims overlap when one key equals or is an ancestor of the other;
overlapping claims always share at least one locked row, so concurrent barriers on
overlapping resources serialize, and the compatibility check sees committed state.

Cross-root compatibility (documented isolation, A16):
    READ/READ ok - READ/WRITE conflict - WRITE/WRITE conflict - EXCLUSIVE/anything conflict
Prepared reads do not get isolation by themselves: freshness is re-checked at the barrier.

Budget scopes keep an append-only ledger: HOLD at the barrier (requested amount),
an extra HOLD up to a conservative bound when an outcome becomes UNKNOWN, CONSUME
with the *observed* amount when application is evidenced, RELEASE_HOLD only when
non-application is confirmed or the hold is replaced by consumption. Holds and
consumption survive root completion and worker lease expiry.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import BudgetEntryKind, ClaimMode, ReservationStatus
from app.domain.errors import ValidationFailed
from app.persistence.models import BudgetEntryRow, BudgetScopeRow, ReservationRow, ResourceRow

ZERO = Decimal("0.00")


def ancestors(key: str) -> list[str]:
    """Self plus ancestors from depth 2 (the provider segment alone is never claimable)."""
    parts = key.split("/")
    if len(parts) < 2:
        raise ValidationFailed(f"resource {key!r} must have at least two segments", code="INVALID_RESOURCE")
    return ["/".join(parts[:i]) for i in range(2, len(parts) + 1)]


def compatible(a: str, b: str) -> bool:
    modes = {ClaimMode(a), ClaimMode(b)}
    return modes == {ClaimMode.READ}


@dataclass
class ClaimRequest:
    effect_id: UUID
    resource: str
    mode: str


@dataclass
class ReservationConflict:
    resource: str
    mode: str
    held_by_root: UUID
    held_resource: str
    held_mode: str
    effect_id: UUID

    def as_dict(self) -> dict:
        return {"resource": self.resource, "mode": self.mode, "held_by_root": str(self.held_by_root),
                "held_resource": self.held_resource, "held_mode": self.held_mode, "effect_id": str(self.effect_id)}


class ReservationService:
    async def reserve(self, s: AsyncSession, tenant_id: str, root_id: UUID,
                      claims: list[ClaimRequest]) -> list[ReservationConflict]:
        """Lock, check and (if compatible) record reservations. Caller owns the transaction."""
        keys = sorted({k for c in claims for k in ancestors(c.resource)})
        if not keys:
            return []
        await s.execute(pg_insert(ResourceRow).values([{"tenant_id": tenant_id, "key": k} for k in keys])
                        .on_conflict_do_nothing(index_elements=["tenant_id", "key"]))
        rows = (await s.execute(select(ResourceRow).where(ResourceRow.tenant_id == tenant_id, ResourceRow.key.in_(keys))
                                .order_by(ResourceRow.key).with_for_update())).scalars().all()
        by_key = {r.key: r for r in rows}
        conflicts: list[ReservationConflict] = []
        for c in claims:
            anc = ancestors(c.resource)
            held = (await s.execute(select(ReservationRow).where(
                ReservationRow.tenant_id == tenant_id,
                ReservationRow.status.in_([ReservationStatus.ACTIVE, ReservationStatus.RETAINED]),
                ReservationRow.root_id != root_id,
                or_(ReservationRow.resource_key.in_(anc), ReservationRow.resource_key.like(_like_prefix(c.resource))),
            ))).scalars().all()
            for h in held:
                if not compatible(c.mode, h.mode):
                    conflicts.append(ReservationConflict(c.resource, c.mode, h.root_id, h.resource_key, h.mode,
                                                         c.effect_id))
        if conflicts:
            return conflicts
        for c in claims:
            s.add(ReservationRow(tenant_id=tenant_id, resource_id=by_key[c.resource].id, resource_key=c.resource,
                                 root_id=root_id, effect_id=c.effect_id, mode=c.mode,
                                 status=str(ReservationStatus.ACTIVE)))
        return []

    async def release(self, s: AsyncSession, root_id: UUID, retain_effects: Iterable[UUID], reason: str) -> dict:
        retain = set(retain_effects)
        rows = (await s.execute(select(ReservationRow).where(
            ReservationRow.root_id == root_id, ReservationRow.status == ReservationStatus.ACTIVE))).scalars().all()
        released, retained = [], []
        for r in rows:
            if r.effect_id in retain:
                r.status, r.reason = str(ReservationStatus.RETAINED), f"retained: {reason}"
                retained.append(r.resource_key)
            else:
                r.status, r.reason, r.released_at = str(ReservationStatus.RELEASED), reason, func.now()
                released.append(r.resource_key)
        return {"released": sorted(released), "retained": sorted(retained)}

    async def release_retained(self, s: AsyncSession, root_id: UUID, effect_id: UUID, reason: str) -> None:
        rows = (await s.execute(select(ReservationRow).where(
            ReservationRow.root_id == root_id, ReservationRow.effect_id == effect_id,
            ReservationRow.status == ReservationStatus.RETAINED))).scalars().all()
        for r in rows:
            r.status, r.reason, r.released_at = str(ReservationStatus.RELEASED), reason, func.now()


def _like_prefix(key: str) -> str:
    escaped = key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "/%"


@dataclass
class BudgetCheck:
    key: str
    limit: Decimal
    consumed: Decimal
    held: Decimal
    requested: Decimal
    currency: str

    @property
    def available(self) -> Decimal:
        return self.limit - self.consumed - self.held

    @property
    def passed(self) -> bool:
        return self.requested <= self.available

    def as_dict(self) -> dict:
        return {"key": self.key, "limit": str(self.limit), "consumed": str(self.consumed), "held": str(self.held),
                "requested": str(self.requested), "available": str(self.available), "currency": self.currency,
                "passed": self.passed}


class BudgetService:
    async def scope(self, s: AsyncSession, tenant_id: str, key: str, limit: Decimal, currency: str,
                    description: str) -> BudgetScopeRow:
        await s.execute(pg_insert(BudgetScopeRow).values(tenant_id=tenant_id, key=key, currency=currency,
                                                         limit_amount=limit, description=description)
                        .on_conflict_do_nothing(index_elements=["tenant_id", "key"]))
        row = (await s.execute(select(BudgetScopeRow).where(BudgetScopeRow.tenant_id == tenant_id,
                                                            BudgetScopeRow.key == key).with_for_update())).scalar_one()
        if row.currency != currency:
            raise ValidationFailed(f"budget {key} is in {row.currency}, not {currency}", code="CURRENCY_MISMATCH")
        if limit < row.limit_amount:  # never widen a durable limit; tighten conservatively
            row.limit_amount = limit
        return row

    async def balance(self, s: AsyncSession, scope_id: UUID) -> tuple[Decimal, Decimal]:
        rows = (await s.execute(select(BudgetEntryRow.kind, func.coalesce(func.sum(BudgetEntryRow.amount), 0))
                                .where(BudgetEntryRow.scope_id == scope_id).group_by(BudgetEntryRow.kind))).all()
        by = {k: Decimal(str(v)) for k, v in rows}
        consumed = by.get(BudgetEntryKind.CONSUME, ZERO)
        held = by.get(BudgetEntryKind.HOLD, ZERO) - by.get(BudgetEntryKind.RELEASE_HOLD, ZERO)
        return consumed, held

    async def _effect_totals(self, s: AsyncSession, scope_id: UUID, effect_id: UUID) -> dict[str, Decimal]:
        rows = (await s.execute(select(BudgetEntryRow.kind, func.coalesce(func.sum(BudgetEntryRow.amount), 0))
                                .where(and_(BudgetEntryRow.scope_id == scope_id, BudgetEntryRow.effect_id == effect_id))
                                .group_by(BudgetEntryRow.kind))).all()
        return {k: Decimal(str(v)) for k, v in rows}

    async def effect_hold(self, s: AsyncSession, scope_id: UUID, effect_id: UUID) -> Decimal:
        by = await self._effect_totals(s, scope_id, effect_id)
        return by.get(BudgetEntryKind.HOLD, ZERO) - by.get(BudgetEntryKind.RELEASE_HOLD, ZERO)

    async def effect_consumed(self, s: AsyncSession, scope_id: UUID, effect_id: UUID) -> Decimal:
        return (await self._effect_totals(s, scope_id, effect_id)).get(BudgetEntryKind.CONSUME, ZERO)

    def entry(self, s: AsyncSession, scope: BudgetScopeRow, root_id: UUID, effect_id: UUID, kind: BudgetEntryKind,
              amount: Decimal, note: str) -> None:
        if amount <= 0:
            return
        s.add(BudgetEntryRow(scope_id=scope.id, root_id=root_id, effect_id=effect_id, kind=str(kind),
                             amount=amount, currency=scope.currency, note=note))

    async def scopes_for_effect(self, s: AsyncSession, root_id: UUID, effect_id: UUID) -> list[BudgetScopeRow]:
        ids = (await s.execute(select(BudgetEntryRow.scope_id).where(
            BudgetEntryRow.root_id == root_id, BudgetEntryRow.effect_id == effect_id).distinct())).scalars().all()
        if not ids:
            return []
        return list((await s.execute(select(BudgetScopeRow).where(BudgetScopeRow.id.in_(ids))
                                     .order_by(BudgetScopeRow.key).with_for_update())).scalars())
