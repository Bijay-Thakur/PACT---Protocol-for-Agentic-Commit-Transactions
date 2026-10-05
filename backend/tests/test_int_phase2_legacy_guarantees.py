"""Phase 1 guarantees exercised through the authenticated Phase 2 contract."""

from __future__ import annotations

import asyncio
import json
import socket
import uuid

import asyncpg
import httpx
import pytest
import uvicorn
from sqlalchemy import select

from app.api.demo import _make_offboarding
from app.domain.effect import EffectProposal
from app.domain.enums import PrincipalKind
from app.domain.errors import ConcurrencyConflict, StateConflict
from app.domain.transaction import ApprovalRequest, BeginRequest, CommitRequest, OperatorActionRequest
from app.main import create_app
from app.persistence.models import CommitDecisionRow, EffectRow, OperationAttemptRow, ReceiptRow, TransactionRow
from app.worker import Worker

from .conftest import TEST_DB, make_settings, requires_db

pytestmark = requires_db


async def _completed_offboarding(rt):
    cid = f"C-LEG-{uuid.uuid4().hex[:8].upper()}"
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    agent, root = await _make_offboarding(rt, tenant, cid, "143.27", False)
    frozen = await rt.coordinator.prepare(agent, root)
    assert frozen["status"] == "FROZEN", frozen
    return agent, root, frozen["digest"], cid


async def test_concurrent_replay_has_one_binding_decision_and_one_refund(rt):
    agent, root, digest, cid = await _completed_offboarding(rt)

    async def commit():
        try:
            return await rt.coordinator.request_commit(agent, root,
                CommitRequest(revision_digest=digest))
        except StateConflict as exc:
            return exc.code

    results = await asyncio.gather(commit(), commit(), commit())
    assert sum(isinstance(x, dict) and x["status"] == "QUEUED" and
               not x.get("replayed", False) for x in results) == 1, results
    assert all(isinstance(x, dict) or x == "COMMIT_ALREADY_IN_PROGRESS" for x in results)
    async with rt.db.read() as session:
        decisions = (await session.execute(select(CommitDecisionRow).where(
            CommitDecisionRow.transaction_id == root))).scalars().all()
        assert len(decisions) == 1

    worker = Worker(rt)
    for _ in range(100):
        await worker.run_once(root)
        async with rt.db.read() as session:
            state = (await session.get(TransactionRow, root)).state
        if state == "COMMITTED_VERIFIED":
            break
        await asyncio.sleep(0.05)
    assert state == "COMMITTED_VERIFIED", state
    calls = (await rt.http.get("/sim/calls", params={"customer_id": cid,
        "system": "billing", "operation": "create_refund"})).json()
    assert len(calls) == 1


async def test_final_receipt_and_events_are_database_immutable(rt):
    agent, root, digest, _ = await _completed_offboarding(rt)
    assert (await rt.coordinator.request_commit(agent, root,
        CommitRequest(revision_digest=digest)))["status"] == "QUEUED"
    worker = Worker(rt)
    for _ in range(100):
        await worker.run_once(root)
        async with rt.db.read() as session:
            state = (await session.get(TransactionRow, root)).state
        if state == "COMMITTED_VERIFIED":
            break
        await asyncio.sleep(0.05)
    assert state == "COMMITTED_VERIFIED", state
    async with rt.db.read() as session:
        receipt = (await session.execute(select(ReceiptRow).where(
            ReceiptRow.transaction_id == root))).scalar_one()
        before = (receipt.sha256, receipt.payload)
    conn = await asyncpg.connect(TEST_DB.replace("+asyncpg", ""))
    try:
        for statement in (
            "UPDATE receipts SET final_state = 'ABORTED' WHERE transaction_id = $1::uuid",
            "DELETE FROM receipts WHERE transaction_id = $1::uuid",
            "UPDATE transaction_events SET event_type = 'X' WHERE root_id = $1::uuid",
        ):
            with pytest.raises(asyncpg.IntegrityConstraintViolationError):
                await conn.execute(statement, root)
    finally:
        await conn.close()
    async with rt.db.read() as session:
        after = (await session.execute(select(ReceiptRow).where(
            ReceiptRow.transaction_id == root))).scalar_one()
        assert (after.sha256, after.payload) == before


async def test_authenticated_event_history_and_stream(rt):
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    agent = await rt.principals.upsert_principal(tenant, "agent", PrincipalKind.AGENT,
        ["tx:begin"], {"workflows": {"customer_remediation": {"remediation_budget": "100.00"}}})
    root = await rt.manager.begin(agent, BeginRequest(workflow="customer_remediation",
        business_request={"customer_id": "C-SSE", "incident_id": f"INC-{uuid.uuid4().hex[:8]}"}))
    key = await rt.principals.issue_api_key(agent.id)
    app = create_app(make_settings(embedded_worker=False), runtime=rt)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://pact", headers={"Authorization": f"Bearer {key}"}) as client:
            response = await client.get(f"/api/v1/transactions/{root}/events")
            assert response.status_code == 200, response.text
            events = response.json()["events"]
            assert events[0]["event_type"] == "TRANSACTION_CREATED"
            sequences = [e["sequence"] for e in events]
            assert sequences == sorted(set(sequences))

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
            log_level="warning", lifespan="off"))
        server_task = asyncio.create_task(server.serve())
        try:
            for _ in range(100):
                if server.started:
                    break
                await asyncio.sleep(0.05)
            assert server.started
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10,
                                         headers={"Authorization": f"Bearer {key}"}) as http:
                async with http.stream("GET", f"/api/v1/transactions/{root}/events/stream") as response:
                    assert response.status_code == 200
                    assert response.headers["content-type"].startswith("text/event-stream")
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            assert json.loads(line[6:])["event_type"] == "TRANSACTION_CREATED"
                            break
        finally:
            server.should_exit = True
            await server_task


async def test_stale_transaction_writer_is_rejected(rt):
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    agent = await rt.principals.upsert_principal(tenant, "agent", PrincipalKind.AGENT,
        ["tx:begin"], {"workflows": {"customer_remediation": {"remediation_budget": "100.00"}}})
    root = await rt.manager.begin(agent, BeginRequest(workflow="customer_remediation",
        business_request={"customer_id": "C-OCC", "incident_id": f"INC-{uuid.uuid4().hex[:8]}"}))
    first, stale = rt.db.sessionmaker(), rt.db.sessionmaker()
    try:
        a = (await first.execute(select(TransactionRow).where(TransactionRow.id == root))).scalar_one()
        b = (await stale.execute(select(TransactionRow).where(TransactionRow.id == root))).scalar_one()
        a.objective = "new objective"
        await first.commit()
        b.objective = "stale objective"
        with pytest.raises(Exception) as exc:
            await stale.commit()
        assert "StaleData" in type(exc.value).__name__
    finally:
        await first.close()
        await stale.close()

    async def stale_write():
        async with rt.db.uow() as session:
            row = await session.get(TransactionRow, root)
            await session.execute(TransactionRow.__table__.update().where(
                TransactionRow.id == root).values(version=TransactionRow.version + 1))
            row.objective = "lost update"

    with pytest.raises(ConcurrencyConflict):
        await stale_write()


async def test_child_commit_and_premature_operator_recovery_are_rejected(rt):
    agent, root, _, _ = await _completed_offboarding(rt)
    async with rt.db.read() as session:
        child = (await session.execute(select(TransactionRow).where(
            TransactionRow.root_id == root, TransactionRow.parent_id == root))).scalars().first()
        assert child is not None
    child_actor = await rt.principals.by_name(agent.tenant_id, child.actor_id)
    assert child_actor is not None
    with pytest.raises(StateConflict) as exc:
        await rt.coordinator.request_commit(child_actor, child.id,
            CommitRequest(revision_digest="0" * 64))
    assert exc.value.code == "CHILD_CANNOT_COMMIT_INDEPENDENTLY"
    operator = await rt.principals.upsert_principal(agent.tenant_id, "operator", PrincipalKind.OPERATOR,
        ["op:recover"], {})
    for action, code in (("RECONCILE", "NOTHING_TO_RECONCILE"),
                         ("RETRY_RESTORATION", "COMPENSATION_NOT_PERMITTED")):
        with pytest.raises(StateConflict) as exc:
            await rt.coordinator.operator_action(operator, root, OperatorActionRequest(
                action=action, reason="Premature recovery request"))
        assert exc.value.code == code


@pytest.mark.parametrize("mode,expected_attempts", [
    ("unavailable", ["RESPONSE_LOST", "ACCEPTED"]),
    ("timeout_before_apply", ["RESPONSE_LOST", "ACCEPTED"]),
])
async def test_ambiguous_retry_uses_stable_provider_idempotency_key(rt, mode, expected_attempts):
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    cid = f"C-RETRY-{uuid.uuid4().hex[:8].upper()}"
    agent = await rt.principals.upsert_principal(tenant, "agent", PrincipalKind.AGENT,
        ["tx:begin", "tx:propose", "tx:prepare", "tx:commit"],
        {"workflows": {"customer_remediation": {"remediation_budget": "100.00"}}})
    approver = await rt.principals.upsert_principal(tenant, "finance", PrincipalKind.OPERATOR,
        ["op:approve"], {"roles": ["finance_approver"]})
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    charge = (await rt.http.get(f"/sim/state/{cid}")).json()["billing"]["account"]["charges"][0]["id"]
    root = await rt.manager.begin(agent, BeginRequest(workflow="customer_remediation",
        business_request={"customer_id": cid, "incident_id": f"INC-{uuid.uuid4().hex[:8]}"}))
    effect_id, _ = await rt.manager.propose(agent, root, EffectProposal(
        effect_type="billing.refund", slot="refund_line",
        payload={"customer_id": cid, "charge_id": charge, "amount": "50.00"}))
    frozen = await rt.coordinator.prepare(agent, root)
    assert frozen["status"] == "FROZEN", frozen
    (await rt.http.post("/sim/faults", json={"customer_id": cid, "system": "billing",
        "operation": "create_refund", "mode": mode})).raise_for_status()
    await rt.coordinator.approve(approver, root, ApprovalRequest(
        revision_digest=frozen["digest"], reason="Reviewed retry test"))
    assert (await rt.coordinator.request_commit(agent, root,
        CommitRequest(revision_digest=frozen["digest"])))["status"] == "QUEUED"
    worker = Worker(rt)
    for _ in range(100):
        await worker.run_once(root)
        async with rt.db.read() as session:
            state = (await session.get(TransactionRow, root)).state
        if state == "COMMITTED_VERIFIED":
            break
        await asyncio.sleep(0.05)
    assert state == "COMMITTED_VERIFIED", (mode, state)
    async with rt.db.read() as session:
        effect = await session.get(EffectRow, effect_id)
        attempts = (await session.execute(select(OperationAttemptRow).where(
            OperationAttemptRow.effect_id == effect_id).order_by(OperationAttemptRow.started_at))).scalars().all()
        execute_statuses = [a.status for a in attempts if a.kind == "EXECUTE"]
        assert execute_statuses == expected_attempts, [(a.kind, a.status) for a in attempts]
        keys = {a.request.get("idempotency_key") for a in attempts if a.kind == "EXECUTE"}
        assert len(keys) == 1 and None not in keys
        assert effect.application == "APPLIED" and effect.postcondition == "MATCH"
    calls = (await rt.http.get("/sim/calls", params={"customer_id": cid,
        "system": "billing", "operation": "create_refund"})).json()
    assert len(calls) == 2
    world = (await rt.http.get(f"/sim/state/{cid}")).json()
    assert len(world["billing"]["refunds"]) == 1
