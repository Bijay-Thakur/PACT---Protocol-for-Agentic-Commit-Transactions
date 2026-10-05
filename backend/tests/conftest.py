"""Integration fixtures: a real PostgreSQL database, migrated with Alembic.

Set PACT_TEST_DATABASE_URL (e.g. postgresql+asyncpg://pact:pact@localhost:5432/pact_test).
Integration tests are skipped when it is not set; pure unit tests always run.
The test database is DROPPED and recreated at session start.
"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest

TEST_DB = os.environ.get("PACT_TEST_DATABASE_URL")
BACKEND = Path(__file__).resolve().parents[1]

requires_db = pytest.mark.skipif(not TEST_DB, reason="PACT_TEST_DATABASE_URL not set")


def _in_thread(fn) -> None:
    errors: list[BaseException] = []

    def target():
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=target)
    t.start()
    t.join()
    if errors:
        raise errors[0]


@pytest.fixture(scope="session")
def migrated_db() -> str:
    if not TEST_DB:
        pytest.skip("PACT_TEST_DATABASE_URL not set")

    from .db_guard import check_and_mark, write_marker

    async def reset():
        import asyncpg
        conn = await asyncpg.connect(TEST_DB.replace("+asyncpg", ""))
        try:
            await check_and_mark(conn, TEST_DB)  # A45: refuse anything not provably disposable
            await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            await write_marker(conn)
        finally:
            await conn.close()

    def migrate():
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND / "migrations"))
        cfg.attributes["url"] = TEST_DB
        command.upgrade(cfg, "head")

    _in_thread(lambda: asyncio.run(reset()))
    _in_thread(migrate)
    return TEST_DB


def make_settings(**overrides: Any):
    from app.config import Settings

    base = {"database_url": TEST_DB, "adapter_timeout_s": 0.5, "auto_recover_on_startup": False,
            "planner_provider": "deterministic", "planner_share_workflow_catalog": False}
    base.update(overrides)
    return Settings(**base)


@pytest.fixture(scope="session")
async def rt(migrated_db):
    from app.runtime import Runtime

    runtime = Runtime(make_settings())
    await runtime.start()
    yield runtime
    await runtime.stop()


@pytest.fixture(scope="session")
async def client(rt):
    from app.main import create_app

    app = create_app(make_settings(), runtime=rt)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://pact",
                                     timeout=120) as c:
            yield c


# ---------------------------------------------------------------- helpers
async def run_scenario(client: httpx.AsyncClient, name: str, **opts) -> dict[str, Any]:
    r = await client.post(f"/api/v1/demo/run/{name}", json=opts)
    assert r.status_code == 200, r.text
    return r.json()


async def detail(client: httpx.AsyncClient, tx_id: str) -> dict[str, Any]:
    r = await client.get(f"/api/v1/transactions/{tx_id}")
    assert r.status_code == 200, r.text
    return r.json()


async def event_types(client: httpx.AsyncClient, tx_id: str) -> list[str]:
    r = await client.get(f"/api/v1/transactions/{tx_id}/events")
    return [e["event_type"] for e in r.json()["events"]]


async def provider_calls(rt, customer_id: str, system: str | None = None, operation: str | None = None) -> list[dict]:
    params = {"customer_id": customer_id}
    if system:
        params["system"] = system
    if operation:
        params["operation"] = operation
    return (await rt.http.get("/sim/calls", params=params)).json()


async def external(rt, customer_id: str) -> dict[str, Any]:
    return (await rt.http.get(f"/sim/state/{customer_id}")).json()


def effect_by_type(d: dict[str, Any], effect_type: str) -> dict[str, Any]:
    return next(e for e in d["effects"] if e["effect_type"] == effect_type)


async def seed(rt, customer_id: str, profile: str = "standard", faults: list[dict] | None = None) -> None:
    (await rt.http.post("/sim/seed", json={"customer_id": customer_id, "profile": profile})).raise_for_status()
    for f in faults or []:
        (await rt.http.post("/sim/faults", json={**f, "customer_id": customer_id})).raise_for_status()


async def build_prepared(client, rt, customer_id: str, *, faults: list[dict] | None = None,
                         root_overrides: dict | None = None, children: list[dict] | None = None,
                         prepare: bool = True) -> str:
    """Seed a customer and build a cancellation transaction through the public API (no commit)."""
    from app.agents.client import HttpPactClient
    from app.agents.scripted_demo_agents import RootAgent, agents_from_specs
    from app.demo.specs import child_specs, root_spec

    await seed(rt, customer_id, faults=faults)
    pact = HttpPactClient(client)
    agent = RootAgent()
    root_id = await agent.open(pact, {**root_spec(customer_id), **(root_overrides or {})})
    await agent.delegate(pact, root_id, agents_from_specs(children if children is not None else child_specs(customer_id)))
    if prepare:
        await pact.prepare(root_id)
    return root_id
