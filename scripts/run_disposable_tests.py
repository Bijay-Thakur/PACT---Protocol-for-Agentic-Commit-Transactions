"""Run the backend suite against a separately named, guard-checked test DB.

Reads the local development DSN privately, creates only the named disposable
database if absent, then lets the existing pytest fixture perform its guard.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.tests.db_guard import check_static  # noqa: E402


def working_url() -> str:
    if os.environ.get("PACT_DATABASE_URL"):
        return os.environ["PACT_DATABASE_URL"]
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("PACT_DATABASE_URL="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("PACT_DATABASE_URL unavailable")


async def ensure_database(url: str) -> None:
    import asyncpg
    parsed = urlsplit(url.replace("+asyncpg", ""))
    database = parsed.path.lstrip("/")
    admin = urlunsplit((parsed.scheme, parsed.netloc, "/postgres", parsed.query, parsed.fragment))
    conn = await asyncpg.connect(admin)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", database)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{database}"')
            print(f"Created disposable database {database}")
        else:
            print(f"Checking existing disposable database {database} through fixture guard")
    finally:
        await conn.close()


def main() -> int:
    source = working_url()
    parsed = urlsplit(source)
    target = urlunsplit((parsed.scheme, parsed.netloc, "/pact_phase21_test", parsed.query, parsed.fragment))
    check_static(target, [source])
    try:
        asyncio.run(ensure_database(target))
    except Exception as exc:
        print(f"Disposable database setup failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    env = {**os.environ, "PACT_TEST_DATABASE_URL": target}
    cmd = [sys.executable, "-m", "pytest", "-q", *(sys.argv[1:] or ["backend/tests"]), "--basetemp",
           str(ROOT / ".pytest_tmp" / "phase21_pg"), "-p", "no:cacheprovider"]
    return subprocess.call(cmd, cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
