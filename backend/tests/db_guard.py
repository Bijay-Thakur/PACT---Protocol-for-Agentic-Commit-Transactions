"""Guard for the destructive integration-test fixture (A45).

The fixture drops and recreates ``public``. It may only ever run against a
database that is provably disposable. Rules (all must hold):

1. The database name contains ``test``.
2. It is not the owner's working database (``PACT_DATABASE_URL`` from the
   environment or the repository ``.env``), compared by host, port and name.
3. It is empty, OR it already carries the marker table
   ``__pact_disposable_test_db`` (created by this guard), OR the owner adopts it
   once with ``PACT_TEST_DB_CONFIRM=<database name>``.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

MARKER = "__pact_disposable_test_db"
REPO_ROOT = Path(__file__).resolve().parents[2]


class UnsafeTestDatabase(RuntimeError):
    pass


def _parts(url: str) -> tuple[str, int, str]:
    u = urlsplit(url.replace("+asyncpg", ""))
    return (u.hostname or "localhost").lower(), u.port or 5432, (u.path or "/").lstrip("/")


def _working_db_urls() -> list[str]:
    urls = [os.environ["PACT_DATABASE_URL"]] if os.environ.get("PACT_DATABASE_URL") else []
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("PACT_DATABASE_URL=") and line.split("=", 1)[1].strip():
                urls.append(line.split("=", 1)[1].strip())
    return urls


def check_static(test_url: str, working_urls: list[str] | None = None) -> None:
    host, port, name = _parts(test_url)
    if "test" not in name.lower():
        raise UnsafeTestDatabase(f"refusing destructive fixture: database {name!r} does not look like a test database")
    for w in working_urls if working_urls is not None else _working_db_urls():
        if _parts(w) == (host, port, name):
            raise UnsafeTestDatabase(
                f"refusing destructive fixture: {name!r} on {host}:{port} is the configured PACT_DATABASE_URL")


async def check_and_mark(conn, test_url: str) -> None:
    """Run against an open asyncpg connection to the test database, before any reset."""
    check_static(test_url)
    _, _, name = _parts(test_url)
    tables = {r["tablename"] for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    if tables and MARKER not in tables and os.environ.get("PACT_TEST_DB_CONFIRM") != name:
        raise UnsafeTestDatabase(
            f"refusing destructive fixture: {name!r} contains {len(tables)} table(s) and no disposable marker. "
            f"If this database is disposable, run once with PACT_TEST_DB_CONFIRM={name}.")


async def write_marker(conn) -> None:
    await conn.execute(f"CREATE TABLE IF NOT EXISTS {MARKER} (created_at timestamptz DEFAULT now())")
