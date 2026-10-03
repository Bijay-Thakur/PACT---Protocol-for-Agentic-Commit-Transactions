"""API contracts, trust boundaries, and fault-injection paths not covered by the acceptance suite."""

from __future__ import annotations

import asyncio
import json
import uuid

from app.demo.specs import child_specs, op_keys, root_spec

from .conftest import build_prepared, detail, effect_by_type, external, provider_calls, requires_db, seed

pytestmark = requires_db


def _cid(tag: str) -> str:
    return f"C-API-{tag}-{uuid.uuid4().hex[:4].upper()}"


async def test_children_cannot_commit_independently(client, rt):
    root_id = await build_prepared(client, rt, _cid("CHILD"))
    child = next(t for t in (await detail(client, root_id))["tree"] if t["parent_id"])
    r = await client.post(f"/api/v1/transactions/{child['id']}/commit")
    assert r.status_code == 409 and r.json()["error"]["code"] == "CHILD_CANNOT_COMMIT_INDEPENDENTLY"


async def test_proposal_validation_and_actor_binding(client, rt):
    cid = _cid("VAL")
    root_id = await build_prepared(client, rt, cid, children=[], prepare=False)
    billing = [s for s in child_specs(cid) if s["actor_id"] == "billing_agent"][0]
    child = (await client.post(f"/api/v1/transactions/{root_id}/children", json={
        "actor_id": "billing_agent", "objective": "x", "capability": billing["capability"]})).json()["transaction"]["id"]
    proposal = billing["effects"][0]

    impostor = await client.post(f"/api/v1/transactions/{child}/effects", json={**proposal, "actor_id": "crm_agent"})
    assert impostor.status_code == 403 and impostor.json()["error"]["code"] == "ACTOR_NOT_BOUND_TO_TRANSACTION"
    bad = await client.post(f"/api/v1/transactions/{child}/effects",
                            json={**proposal, "payload": {"customer_id": cid, "amount": "-5"}})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "EFFECT_PAYLOAD_INVALID"
    unknown = await client.post(f"/api/v1/transactions/{child}/effects", json={**proposal, "effect_type": "wire.transfer"})
    assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "UNKNOWN_EFFECT_TYPE"

    ok = await client.post(f"/api/v1/transactions/{child}/effects", json=proposal)
    assert ok.status_code == 201
    await client.post(f"/api/v1/transactions/{child}/prepare")
    late = await client.post(f"/api/v1/transactions/{child}/effects",
                             json={**proposal, "operation_key": proposal["operation_key"] + "-2"})
    assert late.status_code == 409 and late.json()["error"]["code"] == "SPECIFICATION_CLOSED"


async def test_local_authority_failure_aborts_child_and_blocks_root(client, rt):
    cid = _cid("LOCAL")
    specs = child_specs(cid, refund_amount="250.00", billing_cap="200.00")  # proposal exceeds own cap
    root_id = await build_prepared(client, rt, cid, children=specs)
    d = await detail(client, root_id)
    billing_child = next(t for t in d["tree"] if t["actor_id"] == "billing_agent")
    assert billing_child["state"] == "ABORTED"
    res = (await client.post(f"/api/v1/transactions/{root_id}/commit")).json()
    assert res["state"] == "ABORTED"
    assert "CHILD_NOT_PREPARED:billing_agent" in res["decision"]["blocking_reasons"]
    assert await provider_calls(rt, cid) == []


async def test_approval_policy_gates_commit(client, rt):
    cid = _cid("APPROVE")
    blocked = await build_prepared(client, rt, cid, root_overrides={"requires_approval": True})
    res = (await client.post(f"/api/v1/transactions/{blocked}/commit")).json()
    assert "APPROVAL_REQUIRED" in res["decision"]["blocking_reasons"]

    cid2 = _cid("APPROVE")
    approved = await build_prepared(client, rt, cid2, root_overrides={"requires_approval": True})
    r = await client.post(f"/api/v1/transactions/{approved}/operator-actions",
                          json={"operator_id": "finance-lead", "action": "APPROVE", "note": "refund reviewed"})
    assert r.status_code == 200
    res = (await client.post(f"/api/v1/transactions/{approved}/commit")).json()
    assert res["state"] == "COMMITTED_VERIFIED"
    receipt = (await client.get(f"/api/v1/transactions/{approved}/receipt")).json()["payload"]
    assert receipt["human_actions"][0]["operator_id"] == "finance-lead"


async def test_retryable_provider_rejection_reuses_idempotency_key(client, rt):
    cid = _cid("RETRY")
    root_id = await build_prepared(client, rt, cid, faults=[
        {"system": "billing", "operation": "create_refund", "mode": "unavailable"}])
    res = (await client.post(f"/api/v1/transactions/{root_id}/commit")).json()
    assert res["state"] == "COMMITTED_VERIFIED"
    refund = effect_by_type(await detail(client, root_id), "billing.refund")
    assert [(a["kind"], a["status"]) for a in refund["attempts"]] == [
        ("EXECUTE", "REJECTED_RETRYABLE"), ("EXECUTE", "ACCEPTED")]
    calls = await provider_calls(rt, cid, "billing", "create_refund")
    assert len(calls) == 2 and len({c["idempotency_key"] for c in calls}) == 1
    assert len((await external(rt, cid))["billing"]["refunds"]) == 1


async def test_unknown_not_applied_is_safe_to_retry(client, rt):
    cid = _cid("SAFE")
    root_id = await build_prepared(client, rt, cid, faults=[
        {"system": "billing", "operation": "create_refund", "mode": "timeout_before_apply"}],
        root_overrides={"recovery_policy": {"auto_reconcile": True}})
    res = (await client.post(f"/api/v1/transactions/{root_id}/commit")).json()
    assert res["state"] == "COMMITTED_VERIFIED"
    refund = effect_by_type(await detail(client, root_id), "billing.refund")
    assert [(a["kind"], a["status"]) for a in refund["attempts"]] == [
        ("EXECUTE", "RESPONSE_LOST"), ("RECONCILE", "FOUND_NOT_APPLIED"), ("EXECUTE", "ACCEPTED")]
    assert refund["reconciliation_result"]["outcome"] == "SAFE_TO_RETRY"
    assert len((await external(rt, cid))["billing"]["refunds"]) == 1


async def test_ambiguous_500_after_apply_is_unknown_then_verified(client, rt):
    cid = _cid("AMBIG")
    root_id = await build_prepared(client, rt, cid, faults=[
        {"system": "subscription", "operation": "cancel", "mode": "server_error_after_apply"}],
        root_overrides={"recovery_policy": {"auto_reconcile": True}})
    res = (await client.post(f"/api/v1/transactions/{root_id}/commit")).json()
    assert res["state"] == "COMMITTED_VERIFIED"
    cancel = effect_by_type(await detail(client, root_id), "subscription.cancel")
    assert cancel["dispatch_result"]["http_status"] == 500
    assert cancel["reconciliation_result"]["outcome"] == "VERIFIED_SUCCESS"
    assert len(await provider_calls(rt, cid, "subscription", "cancel")) == 1


async def test_contracts_planner_and_explainer(client, rt):
    contracts = {c["effect_type"]: c for c in (await client.get("/api/v1/contracts")).json()["contracts"]}
    assert contracts["billing.refund"]["reversibility_class"] == "IRREVERSIBLE"
    assert contracts["subscription.cancel"]["compensation_strategy"] == "INVERSE_OPERATION"
    assert contracts["crm.update"]["reversibility_class"] == "REVERSIBLE"

    cid = _cid("PLAN")
    proposal = (await client.post("/api/v1/planner/propose",
                                  json={"intent": f"Cancel customer {cid} and refund the unused period"})).json()
    spec = proposal["proposed_spec"]
    assert proposal["provider"] == "deterministic" and spec["metadata"]["customer_id"] == cid
    # A model (or anyone) inflating authority is stopped by deterministic validation.
    inflated = {**spec, "capability": {**spec["capability"], "amount_limit": "9999999"}}
    r = await client.post("/api/v1/transactions", json=inflated)
    assert r.status_code == 403 and r.json()["error"]["code"] == "ROOT_GRANT_REJECTED"
    await seed(rt, cid)
    created = (await client.post("/api/v1/transactions", json=spec)).json()["transaction"]["id"]
    await client.post(f"/api/v1/transactions/{created}/prepare")
    explained = (await client.get(f"/api/v1/planner/explain/{created}")).json()
    assert explained["advisory"] is True and explained["decision_eligible"] is True
    assert (await client.post(f"/api/v1/transactions/{created}/commit")).json()["state"] == "COMMITTED_VERIFIED"


async def test_event_stream_and_ordering(client, rt):
    root_id = await build_prepared(client, rt, _cid("SSE"))
    events = (await client.get(f"/api/v1/transactions/{root_id}/events")).json()["events"]
    seqs = [e["sequence"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert events[0]["event_type"] == "TRANSACTION_CREATED"

    # ASGITransport buffers whole responses, so serve the stream through a real uvicorn server.
    import socket

    import httpx
    import uvicorn

    from app.main import create_app

    from .conftest import make_settings

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(create_app(make_settings(), runtime=rt), host="127.0.0.1", port=port,
                                           log_level="warning", lifespan="on"))
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.05)

        async def first_event():
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as http:
                async with http.stream("GET", f"/api/v1/transactions/{root_id}/events/stream") as resp:
                    assert resp.headers["content-type"].startswith("text/event-stream")
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            return json.loads(line[6:])

        ev = await asyncio.wait_for(first_event(), timeout=10)
        assert ev["event_type"] == "TRANSACTION_CREATED"
    finally:
        server.should_exit = True
        await task


async def test_reconcile_and_compensate_require_the_right_state(client, rt):
    root_id = await build_prepared(client, rt, _cid("STATE"))
    r = await client.post(f"/api/v1/transactions/{root_id}/reconcile")
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOTHING_TO_RECONCILE"
    r = await client.post(f"/api/v1/transactions/{root_id}/compensate")
    assert r.status_code == 409 and r.json()["error"]["code"] == "COMPENSATION_NOT_PERMITTED"
    assert (await client.get(f"/api/v1/transactions/{uuid.uuid4()}")).status_code == 404


async def test_operator_retry_compensation_after_partial_failure(client, rt):
    r = (await client.post("/api/v1/demo/run/compensation-failure", json={})).json()
    assert r["state"] == "HUMAN_REQUIRED"
    # Ops fixed the subscription system; the reactivate fault was single-use. Retry compensation.
    res = (await client.post(f"/api/v1/transactions/{r['root_id']}/compensate")).json()
    assert res["state"] == "COMPENSATED"
    d = await detail(client, r["root_id"])
    assert effect_by_type(d, "subscription.cancel")["state"] == "COMPENSATED"
    assert (await external(rt, r["customer_id"]))["subscription"]["status"] == "active"
    assert op_keys(r["customer_id"])["cancel"] in {e["operation_key"] for e in d["effects"]}


async def test_root_spec_with_nested_children_is_atomic(client, rt):
    cid = _cid("ATOMIC")
    spec = {**root_spec(cid), "children": child_specs(cid)}
    spec["children"][3]["capability"]["amount_limit"] = "99999"  # one bad delegation
    r = await client.post("/api/v1/transactions", json=spec)
    assert r.status_code == 403
    listed = (await client.get("/api/v1/transactions?limit=500")).json()["transactions"]
    assert cid not in {t["customer_id"] for t in listed}  # nothing half-created
