"""A45: the destructive fixture refuses non-disposable databases."""

import pytest

from .db_guard import UnsafeTestDatabase, check_static

WORK = "postgresql+asyncpg://u:p@localhost:5432/pact"


def test_name_must_look_like_test_db():
    with pytest.raises(UnsafeTestDatabase):
        check_static("postgresql+asyncpg://u:p@localhost:5432/pact_prod", [WORK])


def test_refuses_the_working_database_even_if_named_test():
    work = "postgresql+asyncpg://u:p@db.local:6543/pact_test"
    with pytest.raises(UnsafeTestDatabase):
        check_static("postgresql://x:y@DB.LOCAL:6543/pact_test", [work])


def test_accepts_distinct_test_database():
    check_static("postgresql+asyncpg://u:p@localhost:55432/pact_test", [WORK])
    check_static("postgresql+asyncpg://u:p@localhost:5432/pact_test", [WORK])  # same server, different DB


class FakeConn:
    def __init__(self, tables):
        self.tables = tables

    async def fetch(self, sql):
        return [{"tablename": t} for t in self.tables]


async def test_populated_database_without_marker_is_refused(monkeypatch):
    from .db_guard import check_and_mark

    monkeypatch.delenv("PACT_TEST_DB_CONFIRM", raising=False)
    monkeypatch.setenv("PACT_DATABASE_URL", WORK)
    url = "postgresql+asyncpg://u:p@localhost:55432/pact_test"
    with pytest.raises(UnsafeTestDatabase):
        await check_and_mark(FakeConn(["customers", "orders"]), url)
    await check_and_mark(FakeConn([]), url)  # empty is fine
    await check_and_mark(FakeConn(["transactions", "__pact_disposable_test_db"]), url)  # marked is fine
    monkeypatch.setenv("PACT_TEST_DB_CONFIRM", "pact_test")
    await check_and_mark(FakeConn(["customers"]), url)  # explicit one-time adoption
