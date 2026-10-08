"""Full authenticated accepted-intent flow against disposable PostgreSQL and simulated providers."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.domain.enums import PrincipalKind
from app.persistence.models import PlanRevisionRow, ResidualObligationRow
from app.worker import Worker

from .conftest import requires_db

pytestmark = requires_db


@pytest.mark.parametrize("mode", ["normal", "final_mismatch", "legacy_frozen",
                                  "legacy_inflight", "underfunded"])
async def test_intent_accept_assemble_review_separate_approval_commit_receipt(client, rt,
                                                                             monkeypatch, mode):
    tenant = f"phase21-{uuid.uuid4().hex[:8]}"
    cid = f"C-FLOW-{uuid.uuid4().hex[:6].upper()}"
    requester = await rt.principals.upsert_principal(tenant, "requester", PrincipalKind.OPERATOR,
        ["planner:propose", "tx:begin", "tx:propose", "tx:prepare", "tx:commit"],
        {"workflows": {"customer_offboarding": {"amount_limit": "1.00" if mode == "underfunded" else "500.00",
                                                  "approval_threshold": "100.00"}}})
    approver = await rt.principals.upsert_principal(tenant, "approver", PrincipalKind.OPERATOR,
        ["op:approve", "tx:read_all"], {"roles": ["refund_approver"]})
    requester_headers = {"Authorization": f"Bearer {await rt.principals.issue_api_key(requester.id)}"}
    approver_headers = {"Authorization": f"Bearer {await rt.principals.issue_api_key(approver.id)}"}
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    contradictory = await client.post("/api/v1/planner/propose", headers=requester_headers,
        json={"intent": f"Cancel customer {cid} but keep premium"})
    assert contradictory.status_code == 200
    assert contradictory.json()["intent_issues"][0]["code"] == "CONTRADICTORY_OUTCOME"
    bad_review = await client.post("/api/v1/planner/review", headers=requester_headers,
                                   json={"proposal_trace_id": contradictory.json()["proposal_trace_id"],
                                         "proposal": contradictory.json()["proposed_plan"]})
    assert bad_review.json()["status"] == "NEEDS_CLARIFICATION"
    proposed = await client.post("/api/v1/planner/propose", headers=requester_headers,
        json={"intent": f"Cancel customer {cid} and refund the unused period"})
    assert proposed.status_code == 200, proposed.text
    proposal = proposed.json()
    assert proposal["provider"] == "deterministic_fixture" and proposal["proposal_trace_id"]
    reviewed = await client.post("/api/v1/planner/review", headers=requester_headers,
                                 json={"proposal_trace_id": proposal["proposal_trace_id"],
                                       "proposal": proposal["proposed_plan"]})
    assert reviewed.status_code == 200 and reviewed.json()["status"] == "REVIEWABLE_REQUEST", reviewed.text
    request_id = uuid.uuid4().hex
    body = {"proposal_trace_id": proposal["proposal_trace_id"],
            "business_request": {"customer_id": cid},
            "clarified_objective": f"Cancel {cid} and refund full eligible balance",
            "clarification_note": "Reviewed customer identity and request"}
    accepted = await client.post("/api/v1/planner/accept",
        headers={**requester_headers, "X-PACT-Request-ID": request_id}, json=body)
    assert accepted.status_code == 200, accepted.text
    root_id = accepted.json()["transaction_id"]
    replay = await client.post("/api/v1/planner/accept",
        headers={**requester_headers, "X-PACT-Request-ID": request_id}, json=body)
    assert replay.status_code == 200 and replay.json()["transaction_id"] == root_id
    conflict = await client.post("/api/v1/planner/accept",
        headers={**requester_headers, "X-PACT-Request-ID": request_id},
        json={**body, "clarified_objective": "different request"})
    assert conflict.status_code == 409, conflict.text
    assembled = await client.post(f"/api/v1/planner/assemble/{root_id}", headers=requester_headers)
    assert assembled.status_code == 200 and assembled.json()["status"] == "ASSEMBLED", assembled.text
    assert assembled.json()["trusted_eligible_refund"] == "143.27"
    frozen = await client.post(f"/api/v1/transactions/{root_id}/prepare", headers=requester_headers)
    assert frozen.status_code == 200, frozen.text
    if mode == "underfunded":
        assert frozen.json()["status"] == "REJECTED"
        assert any(issue["code"] == "LOCAL_PREPARE_FAILED" for issue in frozen.json()["issues"])
        calls = (await rt.http.get("/sim/calls", params={"customer_id": cid,
                                                        "operation": "create_refund"})).json()
        assert calls == []
        return
    assert frozen.json()["status"] == "FROZEN", frozen.text
    digest = frozen.json()["digest"]
    projection = await client.get(f"/api/v1/transactions/{root_id}/projection", headers=requester_headers)
    assert projection.status_code == 200 and projection.json()["digest"] == digest
    assert projection.json()["required_outcomes"][0]["amount"] == "143.27"
    if mode == "legacy_frozen":
        async with rt.db.uow() as session:
            revision = (await session.execute(select(PlanRevisionRow).where(
                PlanRevisionRow.root_id == uuid.UUID(root_id),
                PlanRevisionRow.revision_no == 1))).scalar_one()
            revision.policy_version = "offboarding-policy/old"
        held = await client.post(f"/api/v1/transactions/{root_id}/commit", headers=requester_headers,
                                 json={"revision_digest": digest})
        assert held.status_code == 409 and held.json()["error"]["code"] == "POLICY_REVISION_REQUIRED"
        reopened = await client.post(f"/api/v1/transactions/{root_id}/revise", headers=requester_headers,
                                     json={"reason": "Adopt current offboarding policy"})
        assert reopened.status_code == 200 and reopened.json()["state"] == "SPECIFYING"
        current = await client.post(f"/api/v1/transactions/{root_id}/prepare", headers=requester_headers)
        assert current.status_code == 200 and current.json()["status"] == "FROZEN", current.text
        async with rt.db.read() as session:
            new_revision = (await session.execute(select(PlanRevisionRow).where(
                PlanRevisionRow.root_id == uuid.UUID(root_id),
                PlanRevisionRow.revision_no == 2))).scalar_one()
            assert new_revision.policy_version == rt.workflows.get("customer_offboarding").policy_version
        awaiting_new = await client.post(f"/api/v1/transactions/{root_id}/commit", headers=requester_headers,
                                         json={"revision_digest": current.json()["digest"]})
        assert awaiting_new.status_code == 200 and awaiting_new.json()["status"] == "AWAITING_APPROVAL"
        return
    awaiting = await client.post(f"/api/v1/transactions/{root_id}/commit", headers=requester_headers,
                                 json={"revision_digest": digest})
    assert awaiting.status_code == 200 and awaiting.json()["status"] == "AWAITING_APPROVAL"
    approved = await client.post(f"/api/v1/transactions/{root_id}/approve", headers=approver_headers,
                                 json={"revision_digest": digest, "reason": "Reviewed exact consequences"})
    assert approved.status_code == 200, approved.text
    queued = await client.post(f"/api/v1/transactions/{root_id}/commit", headers=requester_headers,
                               json={"revision_digest": digest})
    assert queued.status_code == 200 and queued.json()["status"] == "QUEUED", queued.text
    if mode == "legacy_inflight":
        async with rt.db.uow() as session:
            revision = (await session.execute(select(PlanRevisionRow).where(
                PlanRevisionRow.root_id == uuid.UUID(root_id),
                PlanRevisionRow.revision_no == 1))).scalar_one()
            revision.policy_version = "offboarding-policy/old"
    if mode == "final_mismatch":
        original_final = rt.coordinator.verifier.observe_final

        async def wrong_final_amount(view):
            observation, attempt_no = await original_final(view)
            if view.effect_type == "billing.refund":
                observation = observation.model_copy(update={"observed_amount": Decimal("1.00")})
            return observation, attempt_no

        monkeypatch.setattr(rt.coordinator.verifier, "observe_final", wrong_final_amount)
    worker = Worker(rt)
    for _ in range(100):
        await worker.run_once(uuid.UUID(root_id))
        detail = await client.get(f"/api/v1/transactions/{root_id}", headers=requester_headers)
        if detail.json()["transaction"]["state"] in {"COMMITTED_VERIFIED", "HUMAN_REQUIRED", "COMPENSATED"}:
            break
    if mode == "final_mismatch":
        assert detail.json()["transaction"]["state"] == "HUMAN_REQUIRED", detail.text
        async with rt.db.read() as session:
            residuals = list((await session.execute(select(ResidualObligationRow).where(
                ResidualObligationRow.root_id == uuid.UUID(root_id)))).scalars())
        assert any(item.kind == "APPLIED_MISMATCH" for item in residuals)
        return
    assert detail.json()["transaction"]["state"] == "COMMITTED_VERIFIED", detail.text
    receipt = await client.get(f"/api/v1/transactions/{root_id}/receipt", headers=requester_headers)
    assert receipt.status_code == 200 and receipt.json()["final_state"] == "COMMITTED_VERIFIED", receipt.text
    calls = (await rt.http.get("/sim/calls", params={"customer_id": cid, "system": "billing"})).json()
    assert sum(c.get("operation") == "create_refund" for c in calls) == 1
