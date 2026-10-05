"""Concurrency safety against real PostgreSQL row locks and constraints."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.demo.specs import budget_child_specs, budget_root_spec, child_specs, root_spec
from app.domain.errors import ConcurrencyConflict
from app.persistence.models import TransactionRow

from .conftest import build_prepared, detail, external, provider_calls, requires_db, seed

pytestmark = requires_db


def _cid(tag: str) -> str:
    return f"C-CONC-{tag}-{uuid.uuid4().hex[:4].upper()}"


async def test_duplicate_commit_requests(client, rt):
    cid = _cid("COMMIT")
    root_id = await build_prepared(client, rt, cid)
    responses = await asyncio.gather(*[client.post(f"/api/v1/transactions/{root_id}/commit") for _ in range(3)])
    ok = [r for r in responses if r.status_code == 200]
    rejected = [r for r in responses if r.status_code == 409]
    assert len(ok) == 1 and len(rejected) == 2
    assert {r.json()["error"]["code"] for r in rejected} <= {"COMMIT_ALREADY_IN_PROGRESS", "COMMIT_NOT_PERMITTED"}
    assert ok[0].json()["state"] == "COMMITTED_VERIFIED"
    d = await detail(client, root_id)
    assert len(d["commit_decisions"]) == 1  # exactly one binding barrier decision
    for e in d["effects"]:
        assert [a["kind"] for a in e["attempts"]] == ["EXECUTE"]
    assert len(await provider_calls(rt, cid, "billing", "create_refund")) == 1


async def test_concurrent_child_budget_usage(client, rt):
    """Three agents delegate, propose and prepare at the same time; the outcome is deterministic."""
    for _ in range(2):
        cid = _cid("BUDGET")
        await seed(rt, cid, profile="budget")
        root = (await client.post("/api/v1/transactions", json=budget_root_spec(cid))).json()["transaction"]["id"]
        specs = budget_child_specs(cid)

        async def child(spec):
            c = await client.post(f"/api/v1/transactions/{root}/children", json={
                "actor_id": spec["actor_id"], "objective": spec["objective"], "capability": spec["capability"]})
            assert c.status_code == 201, c.text
            cid_ = c.json()["transaction"]["id"]
            r = await client.post(f"/api/v1/transactions/{cid_}/effects", json=spec["effects"][0])
            assert r.status_code == 201, r.text
            assert (await client.post(f"/api/v1/transactions/{cid_}/prepare")).json()["state"] == "PREPARED"

        await asyncio.gather(*[child(s) for s in specs])
        await client.post(f"/api/v1/transactions/{root}/prepare")
        res = (await client.post(f"/api/v1/transactions/{root}/commit")).json()
        assert res["state"] == "ABORTED"
        assert res["decision"]["blocking_reasons"] == ["GLOBAL_BUDGET_EXCEEDED", "INVARIANT_FAILED:root_budget"]
        assert await provider_calls(rt, cid) == []


async def test_duplicate_operation_key_race(client, rt):
    """Two independent roots race to commit the same logical refund: exactly one refund happens."""
    cid = _cid("RACE")
    await seed(rt, cid)
    billing = [s for s in child_specs(cid) if s["actor_id"] == "billing_agent"][0]
    billing = {**billing, "effects": [{**billing["effects"][0], "depends_on": []}]}
    base = root_spec(cid)
    spec = {**base, "required_effect_types": ["billing.refund"],
            "invariants": [i for i in base["invariants"] if i["key"] in ("single_customer", "unique_operations")]}

    async def make_root():
        root = (await client.post("/api/v1/transactions", json=spec)).json()["transaction"]["id"]
        c = (await client.post(f"/api/v1/transactions/{root}/children", json={
            "actor_id": "billing_agent", "objective": "refund", "capability": billing["capability"]})).json()
        r = await client.post(f"/api/v1/transactions/{c['transaction']['id']}/effects", json=billing["effects"][0])
        assert r.status_code == 201, r.text
        await client.post(f"/api/v1/transactions/{root}/prepare")
        return root

    roots = await asyncio.gather(make_root(), make_root())  # concurrent proposals of one operation_key
    d1, d2 = [await detail(client, r) for r in roots]
    assert d1["effects"][0]["logical_operation"]["id"] == d2["effects"][0]["logical_operation"]["id"]

    results = await asyncio.gather(*[client.post(f"/api/v1/transactions/{r}/commit") for r in roots])
    states = sorted(r.json()["state"] for r in results)
    assert states == ["ABORTED", "COMMITTED_VERIFIED"]
    loser = next(r.json() for r in results if r.json()["state"] == "ABORTED")
    assert any(x.startswith(("OPERATION_IN_FLIGHT_ELSEWHERE", "OPERATION_ALREADY_VERIFIED", "CROSS_TRANSACTION"))
               for x in loser["decision"]["blocking_reasons"])
    assert len((await external(rt, cid))["billing"]["refunds"]) == 1
    assert len(await provider_calls(rt, cid, "billing", "create_refund")) == 1


async def test_optimistic_version_check_rejects_lost_update(client, rt):
    cid = _cid("OCC")
    root_id = uuid.UUID(await build_prepared(client, rt, cid, prepare=False))
    s1, s2 = rt.db.sessionmaker(), rt.db.sessionmaker()
    try:
        a = (await s1.execute(select(TransactionRow).where(TransactionRow.id == root_id))).scalar_one()
        b = (await s2.execute(select(TransactionRow).where(TransactionRow.id == root_id))).scalar_one()
        a.objective = "writer A"
        await s1.commit()
        b.objective = "writer B (stale)"
        with pytest.raises(Exception) as exc:
            await s2.commit()
        assert "StaleData" in type(exc.value).__name__
    finally:
        await s1.close()
        await s2.close()

    async def stale_write():
        async with rt.db.uow() as s:
            row = await s.get(TransactionRow, root_id)
            await s.execute(TransactionRow.__table__.update().where(TransactionRow.id == root_id)
                            .values(version=TransactionRow.version + 1))
            row.objective = "lost update"

    with pytest.raises(ConcurrencyConflict):
        await stale_write()
