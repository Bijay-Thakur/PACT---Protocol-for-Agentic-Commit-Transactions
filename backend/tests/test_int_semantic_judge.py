"""Judge holds, adjudication, stale publication, and restart without another model call."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.agents.semantic_judge import DeterministicSemanticJudge
from app.domain.enums import PrincipalKind
from app.domain.errors import StateConflict
from app.domain.semantics import DimensionAssessment, SemanticAssessment, SemanticIssue, SourceSpan
from app.persistence.models import SemanticAssessmentRow, TransactionRow

from .conftest import requires_db

pytestmark = requires_db


class ConcernJudge(DeterministicSemanticJudge):
    async def assess(self, *, source_text, accepted, plan, candidate_digest):
        assessment = await super().assess(
            source_text=source_text, accepted=accepted, plan=plan, candidate_digest=candidate_digest)
        span = SourceSpan(start=0, end=8, text=source_text[:8])
        issue = SemanticIssue(
            code="JUDGE_CONCERN", dimension="alignment", severity="WARNING",
            detail="advisory paraphrase review; this is not an execution decision",
            source_spans=[span], affected_fields=["objective"],
        )
        return assessment.model_copy(update={
            "aggregate": "REVIEW_REQUIRED",
            "issues": [issue],
            "dimensions": [DimensionAssessment(dimension="alignment", result="CONCERN", issues=[issue])],
        })


class TimeoutJudge(DeterministicSemanticJudge):
    async def assess(self, **kwargs):
        self.calls += 1
        raise TimeoutError()


class ForgedJudge(DeterministicSemanticJudge):
    async def assess(self, *, source_text, accepted, plan, candidate_digest):
        self.calls += 1
        issue = SemanticIssue(
            code="JUDGE_CONCERN", dimension="alignment", detail="forged",
            source_spans=[SourceSpan(start=0, end=4, text="nope")],
        )
        return SemanticAssessment(
            candidate_digest=candidate_digest, aggregate="PASS",
            dimensions=[DimensionAssessment(dimension="alignment", result="CONCERN", issues=[issue])],
            provider=self.name, model=self.model, prompt_version=self.prompt_version,
            rubric_version=self.rubric_version, configuration_version=self.configuration_version,
            issues=[issue],
        )


async def _draft(client, rt, intent: str):
    tenant = f"judge-{uuid.uuid4().hex[:8]}"
    cid = f"C-JUDGE-{uuid.uuid4().hex[:6].upper()}"
    requester = await rt.principals.upsert_principal(
        tenant, "requester", PrincipalKind.OPERATOR,
        ["planner:propose", "tx:begin", "tx:propose", "tx:prepare", "tx:commit"],
        {"workflows": {"customer_offboarding": {"amount_limit": "500.00", "approval_threshold": "100.00"}}})
    approver = await rt.principals.upsert_principal(
        tenant, "approver", PrincipalKind.OPERATOR, ["op:approve", "tx:read_all"], {"roles": ["refund_approver"]})
    requester_headers = {"Authorization": f"Bearer {await rt.principals.issue_api_key(requester.id)}"}
    approver_headers = {"Authorization": f"Bearer {await rt.principals.issue_api_key(approver.id)}"}
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    proposed = await client.post("/api/v1/planner/propose", headers=requester_headers, json={"intent": intent})
    assert proposed.status_code == 200, proposed.text
    body = proposed.json()
    accepted = await client.post("/api/v1/planner/accept", headers={
        **requester_headers, "X-PACT-Request-ID": uuid.uuid4().hex}, json={
            "proposal_trace_id": body["proposal_trace_id"],
            "business_request": {"customer_id": cid},
            "clarified_objective": f"Cancel {cid} and refund the unused period",
            "clarification_note": "Accepted the business request after review",
            "resolved_issue_codes": [issue["code"] for issue in body["intent_issues"]],
        })
    assert accepted.status_code == 200, accepted.text
    root_id = accepted.json()["transaction_id"]
    assembled = await client.post(f"/api/v1/planner/assemble/{root_id}", headers=requester_headers)
    assert assembled.status_code == 200, assembled.text
    return requester, requester_headers, approver_headers, root_id, cid


async def test_false_concern_holds_then_issue_specific_adjudication_can_prepare(client, rt):
    source = "Cancel customer C-JUDGE-HOLD and refund the unused period"
    rt.coordinator.judge = ConcernJudge()
    try:
        _, headers, approver_headers, root_id, cid = await _draft(client, rt, source)
        held = await client.post(f"/api/v1/transactions/{root_id}/prepare", headers=headers)
        assert held.status_code == 200, held.text
        assert held.json()["status"] == "NEEDS_CLARIFICATION"
        assert any(issue["code"] == "JUDGE_CONCERN" for issue in held.json()["issues"])
        calls = (await rt.http.get("/sim/calls", params={"customer_id": cid, "operation": "create_refund"})).json()
        assert calls == []
        async with rt.db.read() as session:
            row = (await session.execute(select(SemanticAssessmentRow).where(
                SemanticAssessmentRow.root_id == uuid.UUID(root_id),
                SemanticAssessmentRow.provider == "deterministic_judge"))).scalar_one()
            digest = row.candidate_digest
        generic = await client.post("/api/v1/planner/adjudicate", headers={
            **approver_headers, "X-PACT-Request-ID": uuid.uuid4().hex}, json={
                "root_id": root_id, "candidate_digest": digest, "issue_code": "JUDGE_CONCERN",
                "evidence": {"source_span": {"start": 0, "end": 8, "text": source[:8]}},
                "reason": "looks good to me"})
        assert generic.status_code == 422 and generic.json()["error"]["code"] == "ADJUDICATION_NOT_SPECIFIC"
        denied = await client.post("/api/v1/planner/adjudicate", headers={
            **headers, "X-PACT-Request-ID": uuid.uuid4().hex}, json={
                "root_id": root_id, "candidate_digest": digest, "issue_code": "JUDGE_CONCERN",
                "evidence": {"source_span": {"start": 0, "end": 8, "text": source[:8]}},
                "reason": "Requester cannot dismiss the advisory concern"})
        assert denied.status_code == 403
        recorded = await client.post("/api/v1/planner/adjudicate", headers={
            **approver_headers, "X-PACT-Request-ID": uuid.uuid4().hex}, json={
                "root_id": root_id, "candidate_digest": digest, "issue_code": "JUDGE_CONCERN",
                "evidence": {"source_span": {"start": 0, "end": 8, "text": source[:8]}},
                "reason": "The paraphrase matches the accepted cancellation and refund"})
        assert recorded.status_code == 200, recorded.text
        assert recorded.json()["applied"] is False and recorded.json()["released_authority"] is False
        before = rt.coordinator.judge.calls
        frozen = await client.post(f"/api/v1/transactions/{root_id}/prepare", headers=headers)
        assert frozen.status_code == 200 and frozen.json()["status"] == "FROZEN", frozen.text
        assert rt.coordinator.judge.calls == before
    finally:
        rt.coordinator.judge = DeterministicSemanticJudge()


async def test_timeout_and_forged_output_hold_without_effects_or_extra_calls(client, rt):
    judge = TimeoutJudge()
    rt.coordinator.judge = judge
    try:
        _, headers, _, root_id, cid = await _draft(
            client, rt, "Cancel customer C-JUDGE-TIMEOUT and refund the unused period")
        held = await client.post(f"/api/v1/transactions/{root_id}/prepare", headers=headers)
        assert held.status_code == 200 and held.json()["status"] == "NEEDS_CLARIFICATION", held.text
        assert judge.calls == 2
        again = await client.post(f"/api/v1/transactions/{root_id}/prepare", headers=headers)
        assert again.status_code == 200 and again.json()["status"] == "NEEDS_CLARIFICATION"
        assert judge.calls == 2
        calls = (await rt.http.get("/sim/calls", params={"customer_id": cid, "operation": "create_refund"})).json()
        assert calls == []
        forged = ForgedJudge()
        rt.coordinator.judge = forged
        _, headers, _, root_id, _ = await _draft(
            client, rt, "Cancel customer C-JUDGE-FORGED and refund the unused period")
        forged_hold = await client.post(f"/api/v1/transactions/{root_id}/prepare", headers=headers)
        assert forged_hold.json()["status"] == "NEEDS_CLARIFICATION"
        async with rt.db.read() as session:
            row = (await session.execute(select(SemanticAssessmentRow).where(
                SemanticAssessmentRow.root_id == uuid.UUID(root_id),
                SemanticAssessmentRow.provider == "deterministic_judge"))).scalar_one()
            assert row.aggregate == "UNAVAILABLE"
    finally:
        rt.coordinator.judge = DeterministicSemanticJudge()


async def test_stale_judge_publication_does_not_freeze(client, rt):
    requester, _, _, root_id, cid = await _draft(
        client, rt, "Cancel customer C-JUDGE-STALE and refund the unused period")
    judge = rt.coordinator.judge

    async def late(**kwargs):
        async with rt.db.uow() as session:
            row = await session.get(TransactionRow, uuid.UUID(root_id))
            row.prepare_generation += 50
        return await DeterministicSemanticJudge.assess(judge, **kwargs)

    rt.coordinator.judge.assess = late
    try:
        with pytest.raises(StateConflict) as error:
            await rt.coordinator.prepare(requester, uuid.UUID(root_id))
        assert error.value.code == "STALE_PREPARATION"
        async with rt.db.read() as session:
            root = await session.get(TransactionRow, uuid.UUID(root_id))
            assert root.state != "PREPARED"
        calls = (await rt.http.get("/sim/calls", params={"customer_id": cid, "operation": "create_refund"})).json()
        assert calls == []
    finally:
        rt.coordinator.judge = DeterministicSemanticJudge()
