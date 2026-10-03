from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.persistence.models import Base

target_metadata = Base.metadata


def _url() -> str:
    return context.config.attributes.get("url") or os.environ.get(
        "PACT_DATABASE_URL", "postgresql+asyncpg://pact:pact@localhost:5432/pact")


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
