"""The twelve acceptance tests from the master build prompt (section 35), against real PostgreSQL."""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.demo.specs import child_specs, root_spec

from .conftest import (
    TEST_DB,
    detail,
    effect_by_type,
    event_types,
    external,
    provider_calls,
    requires_db,
    run_scenario,
)

pytestmark = requires_db


async def test_01_successful_commit(client, rt):
    r = await run_scenario(client, "success")
    assert r["state"] == "COMMITTED_VERIFIED"
    d = await detail(client, r["root_id"])
    assert all(e["state"] == "VERIFIED" for e in d["effects"])
    assert all(t["state"] == "COMMITTED_VERIFIED" for t in d["tree"])
    v = (await client.get(f"/api/v1/transactions/{r['root_id']}/receipt/verify")).json()
    assert v["valid"] is True
    receipt = (await client.get(f"/api/v1/transactions/{r['root_id']}/receipt")).json()["payload"]
    assert receipt["final_state"] == "COMMITTED_VERIFIED"
    assert {c["actor_id"] for c in receipt["children"]} == {
        "billing_agent", "subscription_agent", "identity_agent", "crm_agent", "notification_agent"}
    assert all(c["receipt_hash"] for c in receipt["children"])  # root receipt chains child receipts
    world = await external(rt, r["customer_id"])
    assert world["subscription"]["status"] == "cancelled"
    assert "premium" not in world["identity"]["entitlements"]
    assert world["crm"]["lifecycle_state"] == "churned"
    assert [rf["amount"] for rf in world["billing"]["refunds"]] == ["143.27"]
    assert len(world["notification"]["messages"]) == 1


async def test_02_no_effect_before_barrier(client, rt):
    r = await run_scenario(client, "invariant-failure")
    assert r["state"] == "ABORTED"
    assert "INVARIANT_FAILED:refund_within_authorized_amount" in r["commit_decision"]["blocking_reasons"]
    assert await provider_calls(rt, r["customer_id"]) == []  # nothing left PACT
    world = await external(rt, r["customer_id"])
    assert world["billing"]["refunds"] == []
    assert world["billing"]["account"]["charges"][0]["refunded"] == "0.00"
    assert world["subscription"]["status"] == "active"
    d = await detail(client, r["root_id"])
    assert {e["state"] for e in d["effects"]} == {"ABORTED"}
    receipt = (await client.get(f"/api/v1/transactions/{r['root_id']}/receipt")).json()["payload"]
    assert receipt["commit_decision"]["eligible"] is False
    failed = [i for i in receipt["invariant_results"] if not i["passed"]]
    assert failed[0]["key"] == "refund_within_authorized_amount"
    assert failed[0]["observed"]["effects"][0]["authorized"] == "143.27"


async def test_03_child_authority_escalation_blocked(client):
    cid = "C-ESC-0001"
    root = (await client.post("/api/v1/transactions", json=root_spec(cid))).json()["transaction"]["id"]
    billing = [s for s in child_specs(cid) if s["actor_id"] == "billing_agent"][0]
    for cap_patch, code in [
        ({"allowed_resources": ["customer:*"]}, "RESOURCE_SCOPE_BROADENED"),
        ({"amount_limit": "900.00"}, "AMOUNT_ESCALATION"),
        ({"allowed_effect_types": ["billing.refund", "identity.grant"]}, "EFFECT_TYPE_EXPANSION"),
        ({"delegation_depth": 5}, "DELEGATION_DEPTH_EXCEEDED"),
    ]:
        body = {"actor_id": "billing_agent", "objective": "x", "capability": {**billing["capability"], **cap_patch}}
        resp = await client.post(f"/api/v1/transactions/{root}/children", json=body)
        assert resp.status_code == 403, resp.text
        err = resp.json()["error"]
        assert err["code"] == "DELEGATION_REJECTED"
        assert code in {v["code"] for v in err["details"]}
    assert (await event_types(client, root)).count("CAPABILITY_DELEGATION_REJECTED") == 4
    assert (await detail(client, root))["transaction"]["child_count"] == 0
    # An agent cannot mint root authority by asserting it.
    forged = root_spec("C-ESC-0002")
    forged["capability"]["issuer"] = "agent:billing_agent"
    resp = await client.post("/api/v1/transactions", json=forged)
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "ROOT_GRANT_REJECTED"


async def test_04_cumulative_child_budget_blocked(client, rt):
    r = await run_scenario(client, "budget-conflict")
    assert r["state"] == "ABORTED"
    assert "GLOBAL_BUDGET_EXCEEDED" in r["commit_decision"]["blocking_reasons"]
    d = await detail(client, r["root_id"])
    children = [t for t in d["tree"] if t["parent_id"]]
    assert len(children) == 3
    events = (await client.get(f"/api/v1/transactions/{r['root_id']}/events")).json()["events"]
    prepared = {e["transaction_id"] for e in events
                if e["event_type"] == "TRANSACTION_STATE_CHANGED" and e["payload"]["to"] == "PREPARED"}
    assert {c["id"] for c in children} <= prepared  # every child was individually valid
    budget = next(c for c in d["commit_decisions"][0]["checks"] if c["code"] == "GLOBAL_BUDGET")
    assert budget["observed"]["exposure"] == "11100.00" and budget["observed"]["limit"] == "10000.00"
    locals_ = [c for c in d["commit_decisions"][0]["checks"] if c["code"] == "CUMULATIVE_AUTHORITY"]
    assert len(locals_) == 3 and all(c["passed"] for c in locals_)
    assert await provider_calls(rt, r["customer_id"]) == []


async def test_05_duplicate_operation_suppressed(client, rt):
    r = await run_scenario(client, "duplicate-operation")
    assert r["first_state"] == "COMMITTED_VERIFIED" and r["state"] == "ABORTED"
    assert any(x.startswith("OPERATION_ALREADY_VERIFIED:") for x in r["commit_decision"]["blocking_reasons"])
    world = await external(rt, r["customer_id"])
    assert len(world["billing"]["refunds"]) == 1
    assert len(await provider_calls(rt, r["customer_id"], "billing", "create_refund")) == 1


async def test_06_unknown_reconciles_without_blind_retry(client, rt):
    r = await run_scenario(client, "unknown", pause_at_unknown=True)
    assert r["state"] == "UNKNOWN"
    d = await detail(client, r["root_id"])
    refund = effect_by_type(d, "billing.refund")
    assert refund["state"] == "UNKNOWN"
    assert refund["dispatch_result"]["outcome"] == "RESPONSE_LOST"
    assert refund["logical_operation"]["status"] == "UNKNOWN"
    # The provider DID apply it - PACT just doesn't know yet.
    assert len((await external(rt, r["customer_id"]))["billing"]["refunds"]) == 1
    barrier = (await client.get(f"/api/v1/transactions/{r['root_id']}/commit-decision")).json()
    assert "UNRESOLVED_UNKNOWN" in barrier["blocking_reasons"]

    resp = await client.post(f"/api/v1/transactions/{r['root_id']}/reconcile")
    assert resp.status_code == 200 and resp.json()["state"] == "COMMITTED_VERIFIED"
    d = await detail(client, r["root_id"])
    refund = effect_by_type(d, "billing.refund")
    assert refund["state"] == "VERIFIED"
    assert refund["reconciliation_result"]["outcome"] == "VERIFIED_SUCCESS"
    assert [a["kind"] for a in refund["attempts"]] == ["EXECUTE", "RECONCILE"]
    assert len(await provider_calls(rt, r["customer_id"], "billing", "create_refund")) == 1
    assert len((await external(rt, r["customer_id"]))["billing"]["refunds"]) == 1


async def test_07_verification_differs_from_response(client):
    lost = await run_scenario(client, "unknown")
    refund = effect_by_type(await detail(client, lost["root_id"]), "billing.refund")
    assert refund["dispatch_result"]["outcome"] == "RESPONSE_LOST"
    assert refund["verification_result"]["status"] == "VERIFIED_SUCCESS"  # timeout + verified

    ghost = await run_scenario(client, "verification-mismatch")
    crm = effect_by_type(await detail(client, ghost["root_id"]), "crm.update")
    assert crm["dispatch_result"]["outcome"] == "ACCEPTED" and crm["dispatch_result"]["http_status"] == 200
    assert crm["verification_result"]["status"] == "VERIFIED_FAILURE"  # success + not verified
    assert crm["state"] == "FAILED"
    assert ghost["state"] == "COMPENSATED"


async def test_08_notification_waits_for_verification(client, rt):
    r = await run_scenario(client, "notification-ordering")
    assert r["state"] == "COMMITTED_VERIFIED"
    d = await detail(client, r["root_id"])
    refund, notify = effect_by_type(d, "billing.refund"), effect_by_type(d, "notification.send")
    assert refund["verification_result"]["polls"] > 1  # refund was invisible for a while
    assert notify["dispatched_at"] > refund["verified_at"]
    post = next(i for i in d["invariants"] if i["key"] == "notification_after_verification")
    assert post["evaluations"] and all(ev["passed"] for ev in post["evaluations"])
    types = await event_types(client, r["root_id"])
    assert "VERIFICATION_PENDING" in types
    schedule = [e for e in (await client.get(f"/api/v1/transactions/{r['root_id']}/events")).json()["events"]
                if e["event_type"] == "EXECUTION_SCHEDULE_EVALUATED"]
    assert any(notify["operation_key"] in e["payload"]["waiting_on_verification"] for e in schedule)

    paused = await run_scenario(client, "unknown", pause_at_unknown=True)
    assert await provider_calls(rt, paused["customer_id"], "notification") == []


async def test_09_compensation_truth(client, rt):
    full = await run_scenario(client, "compensation")
    assert full["state"] == "COMPENSATED"
    d = await detail(client, full["root_id"])
    states = {e["effect_type"]: e["state"] for e in d["effects"]}
    assert states == {"subscription.cancel": "COMPENSATED", "crm.update": "COMPENSATED",
                      "identity.revoke": "FAILED", "billing.refund": "ABORTED", "notification.send": "ABORTED"}
    receipt = (await client.get(f"/api/v1/transactions/{full['root_id']}/receipt")).json()["payload"]
    assert {e["operation_key"] for e in receipt["effects"]} == {e["operation_key"] for e in d["effects"]}
    assert [c["operation_key"].split("/")[1].split(":")[0] for c in receipt["compensation_results"]] == ["crm", "subscription"]
    assert receipt["uncompensated_effects"] == []
    assert (await external(rt, full["customer_id"]))["subscription"]["status"] == "active"

    partial = await run_scenario(client, "compensation-failure")
    assert partial["state"] == "HUMAN_REQUIRED"
    draft = await client.get(f"/api/v1/transactions/{partial['root_id']}/receipt")
    assert draft.status_code == 409 and draft.json()["error"]["code"] == "RECEIPT_NOT_FINAL"
    resp = await client.post(f"/api/v1/transactions/{partial['root_id']}/operator-actions",
                             json={"operator_id": "ops-1", "action": "FINALIZE_FAILED",
                                   "note": "subscription to be reactivated manually by support"})
    assert resp.json()["state"] == "FAILED_TERMINAL"
    receipt = (await client.get(f"/api/v1/transactions/{partial['root_id']}/receipt")).json()["payload"]
    comp = {c["operation_key"].split("/")[1].split(":")[0]: c["status"] for c in receipt["compensation_results"]}
    assert comp == {"crm": "ACCEPTED", "subscription": "REJECTED_DEFINITIVE"}
    assert [u["effect_type"] for u in receipt["uncompensated_effects"]] == ["subscription.cancel"]
    assert receipt["human_actions"][0]["action"] == "FINALIZE_FAILED"
    assert "rolled back" not in receipt["outcome_statement"].lower()


async def test_10_restart_recovery_is_covered_in_test_int_recovery():
    """See tests/test_int_recovery.py (in-process crash + real process death)."""


async def test_11_concurrent_commit_requests_is_covered_in_test_int_concurrency():
    """See tests/test_int_concurrency.py::test_duplicate_commit_requests."""


async def test_12_immutable_receipt(client, rt):
    r = await run_scenario(client, "success")
    rid = r["root_id"]
    before = (await client.get(f"/api/v1/transactions/{rid}/receipt")).json()
    for method in ("PUT", "PATCH", "DELETE", "POST"):
        resp = await client.request(method, f"/api/v1/transactions/{rid}/receipt", json={"final_state": "X"})
        assert resp.status_code == 405
    conn = await asyncpg.connect(TEST_DB.replace("+asyncpg", ""))
    try:
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            await conn.execute("UPDATE receipts SET final_state = 'ABORTED' WHERE transaction_id = $1::uuid", rid)
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            await conn.execute("DELETE FROM receipts WHERE transaction_id = $1::uuid", rid)
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            await conn.execute("UPDATE transaction_events SET event_type = 'X' WHERE root_id = $1::uuid", rid)
    finally:
        await conn.close()
    after = (await client.get(f"/api/v1/transactions/{rid}/receipt")).json()
    assert after == before
    # Re-finalizing is idempotent and never rewrites the stored receipt.
    await rt.coordinator.finalize(uuid.UUID(rid))
    assert (await client.get(f"/api/v1/transactions/{rid}/receipt")).json()["sha256"] == before["sha256"]

