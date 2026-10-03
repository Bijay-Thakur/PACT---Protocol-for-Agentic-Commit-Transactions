"""Persistent state of the *simulated external world*.

These tables belong to the simulated providers (billing, subscription, identity,
CRM, notification), not to PACT. PACT never reads them directly: it can only
reach them through adapter HTTP calls, exactly like a real SaaS API. They use a
separate SQLAlchemy metadata and may live in a separate database
(``PACT_SIM_DATABASE_URL``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Identity, Integer, String, delete, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.persistence.db import Database


def _now() -> datetime:
    return datetime.now(UTC)


class SimBase(DeclarativeBase):
    pass


class SimRecord(SimBase):
    __tablename__ = "sim_records"

    system: Mapped[str] = mapped_column(String(32), primary_key=True)
    collection: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(256), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(128), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class SimFault(SimBase):
    __tablename__ = "sim_faults"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    system: Mapped[str] = mapped_column(String(32))
    operation: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(64))
    customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    remaining: Mapped[int] = mapped_column(Integer, default=1)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SimCall(SimBase):
    """Provider-side request log: lets tests prove *how many times* PACT called."""

    __tablename__ = "sim_calls"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    system: Mapped[str] = mapped_column(String(32))
    operation: Mapped[str] = mapped_column(String(64))
    customer_id: Mapped[str] = mapped_column(String(128), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    outcome: Mapped[str] = mapped_column(String(64))
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SimStore:
    def __init__(self, db: Database):
        self.db = db

    async def create_schema(self) -> None:
        async with self.db.engine.begin() as conn:
            await conn.run_sync(SimBase.metadata.create_all)

    # -- records ---------------------------------------------------------
    @staticmethod
    async def get(s: AsyncSession, system: str, collection: str, key: str, *, lock: bool = False) -> SimRecord | None:
        stmt = select(SimRecord).where(
            SimRecord.system == system, SimRecord.collection == collection, SimRecord.key == key
        )
        if lock:
            stmt = stmt.with_for_update()
        return (await s.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def put(s: AsyncSession, system: str, collection: str, key: str, customer_id: str, data: dict[str, Any]) -> None:
        rec = await SimStore.get(s, system, collection, key, lock=True)
        if rec is None:
            s.add(SimRecord(system=system, collection=collection, key=key, customer_id=customer_id, data=data))
        else:
            rec.data = data
        await s.flush()

    @staticmethod
    async def list(s: AsyncSession, system: str, collection: str, customer_id: str) -> list[SimRecord]:
        stmt = (
            select(SimRecord)
            .where(
                SimRecord.system == system,
                SimRecord.collection == collection,
                SimRecord.customer_id == customer_id,
            )
            .order_by(SimRecord.key)
        )
        return list((await s.execute(stmt)).scalars())

    @staticmethod
    async def reset_customer(s: AsyncSession, customer_id: str) -> None:
        await s.execute(delete(SimRecord).where(SimRecord.customer_id == customer_id))
        await s.execute(delete(SimFault).where(SimFault.customer_id == customer_id))
        await s.execute(delete(SimCall).where(SimCall.customer_id == customer_id))

    # -- faults ----------------------------------------------------------
    @staticmethod
    async def take_fault(s: AsyncSession, system: str, operation: str, customer_id: str) -> SimFault | None:
        stmt = (
            select(SimFault)
            .where(
                SimFault.system == system,
                SimFault.operation == operation,
                SimFault.remaining > 0,
                (SimFault.customer_id == customer_id) | (SimFault.customer_id.is_(None)),
            )
            .order_by(SimFault.id)
            .limit(1)
            .with_for_update()
        )
        fault = (await s.execute(stmt)).scalar_one_or_none()
        if fault is not None:
            fault.remaining -= 1
            await s.flush()
        return fault

    @staticmethod
    async def log_call(
        s: AsyncSession, system: str, operation: str, customer_id: str,
        idempotency_key: str | None, outcome: str, request: dict[str, Any],
    ) -> None:
        s.add(SimCall(system=system, operation=operation, customer_id=customer_id,
                      idempotency_key=idempotency_key, outcome=outcome, request=request))
        await s.flush()
