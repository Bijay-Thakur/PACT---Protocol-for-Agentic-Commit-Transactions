"""Serve PACT on the guarded disposable test database for browser checks."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.tests.db_guard import check_and_mark, check_static  # noqa: E402
from scripts.run_disposable_tests import working_url  # noqa: E402


def target_url() -> str:
    source = working_url()
    parsed = urlsplit(source)
    target = urlunsplit((parsed.scheme, parsed.netloc, "/pact_phase21_test", parsed.query, parsed.fragment))
    check_static(target, [source])
    return target


async def check(url: str) -> None:
    import asyncpg
    conn = await asyncpg.connect(url.replace("+asyncpg", ""))
    try:
        await check_and_mark(conn, url)
        version = await conn.fetchval("SELECT version_num FROM alembic_version")
        if version != "0003":
            raise RuntimeError("test database migration is not at 0003")
    finally:
        await conn.close()


def configure() -> str:
    target = target_url()
    asyncio.run(check(target))
    os.environ["PACT_DATABASE_URL"] = target
    os.environ["PACT_SIM_DATABASE_URL"] = target
    os.environ["PACT_MODEL_PROFILE"] = "deterministic_fixture"
    os.environ.pop("PACT_PLANNER_PROVIDER", None)
    os.environ["PACT_DEMO_MODE"] = "false"
    os.environ["PACT_EMBEDDED_WORKER"] = "true"
    os.environ["PACT_CORS_ORIGINS"] = "http://localhost:13000,http://127.0.0.1:13000"
    return target


if __name__ == "__main__":
    configure()
    sys.path.insert(0, str(ROOT / "backend"))
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=18000, log_level="warning")
