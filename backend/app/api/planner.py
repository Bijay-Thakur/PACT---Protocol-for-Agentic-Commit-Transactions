"""Model integration boundary. Proposals only - nothing here can commit."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from sqlalchemy import select, text

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field, field_validator

from app.api.deps import principal, runtime
from app.agents.model_provider import PlanProposal
from app.domain.errors import ValidationFailed, StateConflict
from app.domain.transaction import BeginRequest
from app.agents.semantic_judge import NON_DISMISSABLE_ISSUE_CODES
from app.domain.semantics import SemanticAssessment, compare_intent_to_proposal, extract_intent_semantics
from app.runtime import Runtime
from app.security.principals import Principal
from app.persistence.models import (
    IntentAcceptanceRow, ProposalTraceRow, SemanticAdjudicationRow, SemanticAssessmentRow, TransactionRow,
)

router = APIRouter(prefix="/api/v1/planner", tags=["planner"])


_BYPASS_KEYS = {"semantic_bypass", "skip_semantic_review", "skip_judge"}
_GENERIC_ADJUDICATION = {"looks good", "ok", "okay", "lgtm", "fine", "approved", "approve"}


def _intent_issues(intent: str) -> list[dict[str, str]]:
    issues = []
    if re.search(r"\b(retain|keep)\s+(?:the\s+)?premium\b|\bdo\s+not\s+revoke\s+premium\b",
                 intent, re.IGNORECASE) and re.search(r"\b(cancel|offboard)\b", intent, re.IGNORECASE):
        issues.append({"code": "CONTRADICTORY_OUTCOME",
                       "detail": "offboarding requires premium revocation; clarify the business objective"})
    if re.search(r"ignore\s+(?:all\s+|any\s+|previous\s+|prior\s+)?instructions|\byou are now\b|\bsystem prompt\b",
                 intent, re.IGNORECASE):
        issues.append({"code": "PROMPT_INJECTION",
                       "detail": "instruction-like text is data and cannot grant authority"})
    return issues


def _reject_semantic_bypass(context: dict[str, Any]) -> None:
    if _BYPASS_KEYS & set(context):
        raise ValidationFailed("caller cannot bypass semantic review", code="SEMANTIC_BYPASS_REJECTED")


def _proposal_issues(intent: str, proposal: PlanProposal) -> list[dict[str, Any]]:
    semantics = extract_intent_semantics(intent)
    return [issue.model_dump(mode="json") for issue in compare_intent_to_proposal(semantics, proposal)]


class ProposeRequest(BaseModel):
    intent: str = Field(min_length=3, max_length=2000)
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("context")
    @classmethod
    def bounded_context(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, ensure_ascii=False)) > 8_000:
            raise ValueError("context exceeds 8 KB")
        def check(item: Any, depth: int = 0) -> None:
            if depth > 4:
                raise ValueError("context nesting exceeds four levels")
            if isinstance(item, dict):
                for key, nested in item.items():
                    if not isinstance(key, str) or len(key) > 128:
                        raise ValueError("context key invalid")
                    check(nested, depth + 1)
            elif isinstance(item, list):
                for nested in item:
                    check(nested, depth + 1)
        check(value)
        return value


class ReviewRequest(BaseModel):
    proposal: PlanProposal
    proposal_trace_id: UUID | None = None
    original_intent: str | None = Field(default=None, min_length=3, max_length=2000)


@router.post("/propose")
async def propose(body: ProposeRequest, p: Principal = Depends(principal),
                  rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    _reject_semantic_bypass(body.context)
    if not (p.has("tx:begin") or p.has("planner:propose")):
        p.require("planner:propose")
    live = rt.planner.name != "deterministic_fixture"
    intent_digest = hashlib.sha256(body.intent.encode()).hexdigest()
    trace_id = None
    if live:
        denied = False
        reserve = rt.settings.model_max_output_tokens + rt.settings.model_max_input_chars // 4
        lock_key = int.from_bytes(hashlib.sha256(f"{p.tenant_id}/{rt.planner.name}".encode()).digest()[:8],
                                  "big", signed=True)
        async with rt.db.uow() as session:
            await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
            since = datetime.now(UTC) - timedelta(hours=1)
            recent = list((await session.execute(select(ProposalTraceRow).where(
                ProposalTraceRow.tenant_id == p.tenant_id, ProposalTraceRow.provider == rt.planner.name,
                ProposalTraceRow.created_at >= since))).scalars())
            # Crash/timeout cleanup only frees a concurrency slot. Reserved
            # tokens remain charged because the provider may have processed it.
            stale_before = datetime.now(UTC) - timedelta(seconds=90)
            for row in recent:
                if row.outcome == "ADMITTED" and row.created_at < stale_before:
                    row.outcome = "UNKNOWN_USAGE"
                    row.validation = {"error_code": "PROCESS_LOST_OR_STALLED"}
            used = sum(int((row.usage or {}).get("reserved_tokens", reserve)) for row in recent)
            active = sum(row.outcome == "ADMITTED" for row in recent)
            if (len(recent) >= rt.settings.model_max_requests_per_hour
                    or used + reserve > rt.settings.model_max_reserved_tokens_per_hour
                    or active >= rt.settings.model_max_inflight):
                denied = True
            else:
                trace = ProposalTraceRow(tenant_id=p.tenant_id, principal_id=p.id,
                    provider=rt.planner.name, model=getattr(rt.planner, "model", None), live=True,
                    prompt_version="intent-extract/1", schema_version="plan-proposal/1",
                    intent_digest=intent_digest, proposal=None, validation={}, outcome="ADMITTED",
                    usage={"reserved_tokens": reserve, "disposition": "UNKNOWN"})
                session.add(trace)
                await session.flush()
                trace_id = trace.id
        if denied:
            raise ValidationFailed("model allowance exhausted", code="PLANNER_BUDGET_EXHAUSTED")
    try:
        spec = await rt.planner.propose_transaction(body.intent, body.context)
    except BaseException as exc:
        if trace_id is not None:
            async with rt.db.uow() as session:
                trace = await session.get(ProposalTraceRow, trace_id)
                trace.outcome = "CANCELLED" if isinstance(exc, __import__("asyncio").CancelledError) else "FAILED"
                trace.validation = {"schema_valid": False,
                                    "error_code": exc.code if isinstance(exc, ValidationFailed) else type(exc).__name__}
                trace.latency_ms = getattr(rt.planner, "last_latency_ms", None)
                trace.request_id = getattr(rt.planner, "last_request_id", None)
                trace.usage = {**(trace.usage or {}), **(getattr(rt.planner, "last_usage", None) or {}),
                               "disposition": "OBSERVED" if getattr(rt.planner, "last_usage", None) else "UNKNOWN"}
        raise
    intent_issues = _proposal_issues(body.intent, spec)
    async with rt.db.uow() as session:
        trace = await session.get(ProposalTraceRow, trace_id) if trace_id else ProposalTraceRow(
            tenant_id=p.tenant_id, principal_id=p.id, provider=rt.planner.name,
            model=getattr(rt.planner, "model", None), live=live,
            prompt_version="intent-extract/1", schema_version="plan-proposal/1",
            intent_digest=intent_digest, proposal=None, validation={}, outcome="ADMITTED", usage={})
        trace.source_text = body.intent
        trace.request_id = getattr(rt.planner, "last_request_id", None)
        trace.latency_ms = getattr(rt.planner, "last_latency_ms", None)
        trace.proposal = spec.model_dump(mode="json")
        trace.validation = {"schema_valid": True, "intent_issues": intent_issues}
        trace.outcome = "PROPOSED"
        trace.usage = {**(trace.usage or {}), **(getattr(rt.planner, "last_usage", None) or {}),
                       "disposition": "OBSERVED" if getattr(rt.planner, "last_usage", None) else "UNKNOWN"}
        if trace_id is None:
            session.add(trace)
            await session.flush()
        trace_id = trace.id
    return {
        "proposal_trace_id": str(trace_id),
        "intent_issues": intent_issues,
        "provider": rt.planner.name,
        "live": live,
        "model": getattr(rt.planner, "model", None),
        "usage": getattr(rt.planner, "last_usage", None),
        "request_id": getattr(rt.planner, "last_request_id", None),
        "proposed_plan": spec.model_dump(mode="json"),
        "applied": False,
        "note": "Proposal only; a trusted workflow must resolve facts and compile policy before review.",
    }


class AcceptRequest(BaseModel):
    proposal_trace_id: UUID
    business_request: dict[str, Any]
    clarified_objective: str = Field(min_length=3, max_length=2000)
    clarification_note: str = Field(default="", max_length=1000)
    resolved_issue_codes: list[str] = Field(default_factory=list, max_length=20)


@router.post("/accept")
async def accept(body: AcceptRequest,
                 request_id: str = Header(alias="X-PACT-Request-ID"),
                 p: Principal = Depends(principal), rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    p.require("tx:begin")
    async with rt.db.read() as session:
        trace = await session.get(ProposalTraceRow, body.proposal_trace_id)
        if trace is None or trace.tenant_id != p.tenant_id or trace.principal_id != p.id \
                or trace.outcome != "PROPOSED" or not trace.proposal:
            raise StateConflict("proposal is unavailable for acceptance", code="PROPOSAL_NOT_AVAILABLE")
        proposal = PlanProposal.model_validate(trace.proposal)
    wf = rt.workflows.get(proposal.requested_workflow)
    if any(action not in {slot.name for slot in wf.slots} for action in proposal.candidate_actions):
        raise ValidationFailed("proposal contains unknown actions", code="INTENT_NEEDS_CLARIFICATION")
    if proposal.unresolved_questions and not body.clarification_note.strip():
        raise ValidationFailed("unresolved questions need an explicit clarification note",
                               code="INTENT_NEEDS_CLARIFICATION")
    material_codes = {issue["code"] for issue in (trace.validation or {}).get("intent_issues", [])}
    if material_codes and (
            not body.clarification_note.strip()
            or not material_codes.issubset(set(body.resolved_issue_codes))
            or _intent_issues(body.clarified_objective)):
        raise ValidationFailed("material semantic issues require issue-specific clarification",
                               code="INTENT_NEEDS_CLARIFICATION",
                               details={"unresolved_issue_codes": sorted(
                                   material_codes - set(body.resolved_issue_codes))})
    if any(issue["code"] in {"CRITICAL_ENTITY_MISMATCH", "AMBIGUOUS_CRITICAL_ENTITY"}
           for issue in (trace.validation or {}).get("intent_issues", [])):
        accepted_customer = body.business_request.get("customer_id")
        if not isinstance(accepted_customer, str) or accepted_customer not in set(
                re.findall(r"\bC-[A-Za-z0-9-]+\b", body.clarified_objective)):
            raise ValidationFailed("clarified objective must name the accepted customer",
                                   code="INTENT_NEEDS_CLARIFICATION")
    if p.workflow_grant(wf.key) is None:
        raise ValidationFailed("workflow is not granted to this requester", code="WORKFLOW_NOT_GRANTED")
    params = wf.validate_params(body.business_request)
    if not trace.source_text:
        raise StateConflict("legacy proposal has no reviewable source text",
                            code="SEMANTIC_SOURCE_UNAVAILABLE")
    semantics = extract_intent_semantics(body.clarified_objective)
    semantics.source_text = trace.source_text
    semantics.source_digest = trace.intent_digest
    semantics.source_reference = f"proposal-trace:{trace.id}"
    semantics.entity_identifiers.update({
        key: str(value) for key, value in params.items() if key.endswith("_id")
    })
    semantics.clarifications.append({
        "accepted_objective": body.clarified_objective,
        "note": body.clarification_note,
        "resolved_issue_codes": sorted(set(body.resolved_issue_codes)),
        "principal_id": str(p.id),
    })
    root = await rt.manager.begin(p, BeginRequest(workflow=wf.key, business_request=params,
        objective=body.clarified_objective, labels={"proposal_trace_id": str(body.proposal_trace_id)}),
        request_id=request_id, acceptance=(
            body.proposal_trace_id, body.clarification_note, body.clarified_objective,
            semantics.model_dump(mode="json"), sorted(set(body.resolved_issue_codes)),
        ))
    return {"transaction_id": str(root), "state": "CREATED", "applied": False,
            "next_action": "ASSEMBLE_DRAFT" if rt.assemblers.supports(wf.key) else "PROPOSE_EFFECTS"}


@router.post("/assemble/{root_id}")
async def assemble(root_id: UUID, p: Principal = Depends(principal),
                   rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    p.require("tx:propose")
    async with rt.db.read() as session:
        root = await session.get(TransactionRow, root_id)
        if root is None or root.tenant_id != p.tenant_id or root.principal_id != p.id:
            raise StateConflict("accepted draft unavailable", code="DRAFT_NOT_AVAILABLE")
        workflow = root.workflow
    return await rt.assemblers.assemble(workflow, root_id, p, rt)


@router.post("/review")
async def review(body: ReviewRequest, p: Principal = Depends(principal),
                 rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    if not (p.has("tx:begin") or p.has("planner:propose")):
        p.require("planner:propose")
    proposal = body.proposal
    source_intent = body.original_intent
    if body.proposal_trace_id is not None:
        async with rt.db.read() as session:
            trace = await session.get(ProposalTraceRow, body.proposal_trace_id)
            if trace is None or trace.tenant_id != p.tenant_id or trace.principal_id != p.id \
                    or trace.outcome != "PROPOSED" or not trace.source_text:
                raise StateConflict("proposal trace is unavailable for semantic review",
                                    code="PROPOSAL_TRACE_NOT_AVAILABLE")
            if trace.proposal != proposal.model_dump(mode="json"):
                raise StateConflict("review proposal does not match its immutable trace",
                                    code="PROPOSAL_TRACE_MISMATCH")
            source_intent = trace.source_text
    try:
        wf = rt.workflows.get(proposal.requested_workflow)
    except ValidationFailed:
        return {"status": "NEEDS_CLARIFICATION", "workflow": proposal.requested_workflow,
                "business_request": None,
                "issues": [{"code": "UNKNOWN_WORKFLOW", "workflow": proposal.requested_workflow}],
                "required_slots": [], "authorized_to_begin": False, "applied": False,
                "next_action": "RESOLVE_ISSUES_OR_USE_AUTHORIZED_AGENT"}
    supplied = {**proposal.entity_references, **proposal.requested_parameters}
    allowed = set(wf.params_model.model_fields)
    issues = [{"code": "UNKNOWN_BUSINESS_FIELD", "field": field}
              for field in sorted(set(supplied) - allowed)]
    try:
        params = wf.validate_params({k: v for k, v in supplied.items() if k in allowed})
    except ValidationFailed as exc:
        params = None
        issues.append({"code": exc.code, "details": exc.details})
    slots = {slot.name for slot in wf.slots}
    issues.extend({"code": "UNKNOWN_CANDIDATE_ACTION", "action": action}
                  for action in proposal.candidate_actions if action not in slots)
    issues.extend({"code": "MODEL_UNRESOLVED_QUESTION", "question": question}
                  for question in proposal.unresolved_questions)
    if source_intent is None:
        issues.append({"code": "ORIGINAL_INTENT_REQUIRED",
                       "detail": "semantic review requires an authorized proposal trace"})
    else:
        issues.extend(_proposal_issues(source_intent, proposal))
    grant = p.workflow_grant(wf.key)
    return {"status": "NEEDS_CLARIFICATION" if issues else "REVIEWABLE_REQUEST",
            "workflow": wf.key, "business_request": params, "issues": issues,
            "required_slots": [s.name for s in wf.slots if s.required],
            "authorized_to_begin": grant is not None and p.has("tx:begin"),
            "applied": False,
            "next_action": "BEGIN_AND_PROPOSE_EFFECTS" if not issues and grant is not None and p.has("tx:begin")
                           else "RESOLVE_ISSUES_OR_USE_AUTHORIZED_AGENT"}


class AdjudicateRequest(BaseModel):
    root_id: UUID
    candidate_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    issue_code: str = Field(min_length=3, max_length=64)
    evidence: dict[str, Any]
    reason: str = Field(min_length=12, max_length=1000)


@router.post("/adjudicate")
async def adjudicate(body: AdjudicateRequest,
                     request_id: str = Header(alias="X-PACT-Request-ID"),
                     p: Principal = Depends(principal), rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Dismiss one advisory judge concern. This does not approve, freeze, or dispatch."""

    p.require("op:approve")
    normalized_reason = body.reason.strip().lower()
    if normalized_reason in _GENERIC_ADJUDICATION or any(
            normalized_reason.startswith(prefix) for prefix in _GENERIC_ADJUDICATION):
        raise ValidationFailed("a generic note cannot resolve a semantic issue",
                               code="ADJUDICATION_NOT_SPECIFIC")
    if body.issue_code in NON_DISMISSABLE_ISSUE_CODES:
        raise ValidationFailed("hard semantic and policy issues cannot be dismissed by adjudication",
                               code="ADJUDICATION_NOT_PERMITTED")
    span = body.evidence.get("source_span")
    if not isinstance(span, dict):
        raise ValidationFailed("adjudication requires a cited source span",
                               code="ADJUDICATION_EVIDENCE_REQUIRED")
    async with rt.db.uow() as session:
        root = await session.get(TransactionRow, body.root_id)
        if root is None or root.tenant_id != p.tenant_id:
            raise StateConflict("transaction is unavailable for adjudication", code="TRANSACTION_NOT_FOUND")
        acceptance = (await session.execute(select(IntentAcceptanceRow).where(
            IntentAcceptanceRow.root_id == root.id))).scalar_one_or_none()
        source = "" if acceptance is None or not acceptance.intent_semantics else str(
            acceptance.intent_semantics.get("source_text") or "")
        start, end, text = span.get("start"), span.get("end"), span.get("text")
        if (not isinstance(start, int) or not isinstance(end, int) or not isinstance(text, str)
                or start < 0 or end < start or end > len(source) or not text or source[start:end] != text):
            raise ValidationFailed("adjudication citation does not match the protected source",
                                   code="FORGED_CITATION")
        assessments = list((await session.execute(select(SemanticAssessmentRow).where(
            SemanticAssessmentRow.tenant_id == p.tenant_id,
            SemanticAssessmentRow.root_id == root.id,
            SemanticAssessmentRow.candidate_digest == body.candidate_digest,
        ))).scalars())
        judged = next((row for row in assessments if row.provider != "deterministic_guard"), None)
        if judged is None:
            raise StateConflict("no advisory assessment exists for this candidate", code="ASSESSMENT_NOT_FOUND")
        parsed = SemanticAssessment.model_validate(judged.assessment)
        if body.issue_code not in {issue.code for issue in parsed.issues}:
            raise ValidationFailed("adjudication must name an issue in the advisory assessment",
                                   code="ISSUE_NOT_IN_ASSESSMENT")
        replay = await rt.manager._request_replay(
            session, p, request_id, "semantic-adjudicate", body.model_dump(mode="json"))
        if replay is not None and replay.response:
            return replay.response
        row = SemanticAdjudicationRow(
            tenant_id=p.tenant_id, root_id=root.id, revision_no=root.current_revision,
            candidate_digest=body.candidate_digest, assessment_id=judged.id, principal_id=p.id,
            issue_codes=[body.issue_code], evidence=body.evidence, reason=body.reason,
        )
        session.add(row)
        await session.flush()
        response = {
            "adjudication_id": str(row.id), "candidate_digest": body.candidate_digest,
            "issue_code": body.issue_code, "applied": False, "released_authority": False,
            "next_action": "PREPARE",
        }
        if replay is not None:
            replay.status_code = 200
            replay.response = response
        return response


@router.get("/explain/{tx_id}")
async def explain(tx_id: UUID, p: Principal = Depends(principal),
                  rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    decision = await rt.coordinator.evaluate_barrier(await rt.coordinator.root_of(p, tx_id))
    return {"advisory": True, "explanation": await rt.explainer.explain_decision(decision),
            "decision_eligible": decision.eligible}
