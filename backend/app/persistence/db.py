from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import AsyncIterator
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from sqlalchemy.orm.exc import StaleDataError

from app.domain.errors import ConcurrencyConflict


def _default(o: object) -> object:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, UUID):
        return str(o)
    if isinstance(o, Enum):
        return o.value
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def json_dumps(value: object) -> str:
    return json.dumps(value, default=_default)


def make_engine(url: str, **kw) -> AsyncEngine:
    return create_async_engine(
        url, json_serializer=json_dumps, pool_size=kw.pop("pool_size", 10), max_overflow=20, **kw
    )


class Database:
    """Owns the engine and hands out units of work.

    A unit of work is one PostgreSQL transaction: state changes and their events
    are committed atomically or not at all.
    """

    def __init__(self, url: str, **engine_kw):
        self.engine = make_engine(url, **engine_kw)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def uow(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as session:
            try:
                async with session.begin():
                    yield session
            except StaleDataError as exc:
                raise ConcurrencyConflict(
                    "Aggregate was modified concurrently (optimistic version check failed)",
                    details={"error": str(exc)},
                ) from exc

    @asynccontextmanager
    async def read(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as session:
            async with session.begin():
                yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
