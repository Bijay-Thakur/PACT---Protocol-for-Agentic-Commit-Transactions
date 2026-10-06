"""Model integration boundary. Proposals only - nothing here can commit."""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
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
from app.domain.effect import EffectProposal
from app.runtime import Runtime
from app.security.principals import Principal
from app.persistence.models import ProposalTraceRow, IntentAcceptanceRow, EffectRow, TransactionRow
from app.persistence.repositories import effect_view

router = APIRouter(prefix="/api/v1/planner", tags=["planner"])


def _intent_issues(intent: str) -> list[dict[str, str]]:
    if re.search(r"\b(retain|keep)\s+(?:the\s+)?premium\b|\bdo\s+not\s+revoke\s+premium\b",
                 intent, re.IGNORECASE) and re.search(r"\b(cancel|offboard)\b", intent, re.IGNORECASE):
        return [{"code": "CONTRADICTORY_OUTCOME",
                 "detail": "offboarding requires premium revocation; clarify the business objective"}]
    return []


def _proposal_issues(intent: str, proposal: PlanProposal) -> list[dict[str, str]]:
    issues = _intent_issues(intent)
    named_customers = set(re.findall(r"\bC-[A-Za-z0-9-]+\b", intent))
    if len(named_customers) > 1:
        issues.append({"code": "AMBIGUOUS_CRITICAL_ENTITY",
                       "detail": "request names multiple customers; select one explicitly"})
    elif len(named_customers) == 1:
        expected = next(iter(named_customers))
        proposed = {proposal.entity_references.get("customer_id"),
                    proposal.requested_parameters.get("customer_id")}
        proposed.discard(None)
        if proposed != {expected}:
            issues.append({"code": "CRITICAL_ENTITY_MISMATCH",
                           "detail": "model customer differs from the customer named in the request"})
    return issues


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


@router.post("/propose")
async def propose(body: ProposeRequest, p: Principal = Depends(principal),
                  rt: Runtime = Depends(runtime)) -> dict[str, Any]:
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
    if (trace.validation or {}).get("intent_issues") and (
            not body.clarification_note.strip() or _intent_issues(body.clarified_objective)):
        raise ValidationFailed("contradictory intent requires a clarified objective and note",
                               code="INTENT_NEEDS_CLARIFICATION")
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
    root = await rt.manager.begin(p, BeginRequest(workflow=wf.key, business_request=params,
        objective=body.clarified_objective, labels={"proposal_trace_id": str(body.proposal_trace_id)}),
        request_id=request_id, acceptance=(body.proposal_trace_id, body.clarification_note))
    return {"transaction_id": str(root), "state": "CREATED", "applied": False,
            "next_action": "ASSEMBLE_DRAFT" if wf.key == "customer_offboarding" else "PROPOSE_EFFECTS"}


@router.post("/assemble/{root_id}")
async def assemble(root_id: UUID, p: Principal = Depends(principal),
                   rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    p.require("tx:propose")
    async with rt.db.read() as session:
        root = await session.get(TransactionRow, root_id)
        link = (await session.execute(select(IntentAcceptanceRow).where(
            IntentAcceptanceRow.root_id == root_id))).scalar_one_or_none()
        if root is None or root.tenant_id != p.tenant_id or root.principal_id != p.id \
                or root.workflow != "customer_offboarding" or link is None:
            raise StateConflict("accepted offboarding draft unavailable", code="DRAFT_NOT_AVAILABLE")
        cid = link.clarified_request["customer_id"]
        reason = link.clarified_request.get("reason", "customer requested cancellation")

    async def add(slot: str, effect_type: str, payload: dict[str, Any]) -> UUID:
        effect_id, _ = await rt.manager.propose(p, root_id, EffectProposal(
            effect_type=effect_type, slot=slot, payload=payload),
            request_id=f"assemble:{root_id}:{slot}")
        return effect_id

    cancel_id = await add("cancel_subscription", "subscription.cancel", {"customer_id": cid, "reason": reason})
    async with rt.db.read() as session:
        cancel = await session.get(EffectRow, cancel_id)
        view = effect_view(cancel)
    fact = await rt.registry.adapter("subscription.cancel").prepare(view, rt.exec_ctx.adapter_ctx())
    if not fact.ok or "unused_balance" not in fact.observations:
        return {"transaction_id": str(root_id), "status": "NEEDS_CLARIFICATION",
                "issues": [{"code": "FACT_UNAVAILABLE", "detail": fact.reason}], "applied": False}
    try:
        eligible = Decimal(str(fact.observations["unused_balance"]))
    except InvalidOperation:
        raise ValidationFailed("trusted unused balance invalid", code="FACT_UNAVAILABLE") from None
    if eligible < 0:
        raise ValidationFailed("trusted unused balance invalid", code="FACT_UNAVAILABLE")
    await add("revoke_premium", "identity.revoke", {"customer_id": cid, "entitlement": "premium"})
    await add("mark_churned", "crm.update", {"customer_id": cid,
                                             "lifecycle_state": "churned", "note": "Cancelled via PACT"})
    if eligible > 0:
        await add("refund_unused", "billing.refund", {"customer_id": cid,
                                                        "amount": str(eligible), "reason": "unused-period refund"})
    await add("confirm_customer", "notification.send", {"customer_id": cid,
        "template": "cancellation_confirmation", "variables": {"refund_amount": str(eligible)}})
    return {"transaction_id": str(root_id), "status": "ASSEMBLED",
            "trusted_eligible_refund": str(eligible), "applied": False, "next_action": "PREPARE"}


@router.post("/review")
async def review(body: ReviewRequest, p: Principal = Depends(principal),
                 rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    if not (p.has("tx:begin") or p.has("planner:propose")):
        p.require("planner:propose")
    proposal = body.proposal
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
    issues.extend(_intent_issues(proposal.objective))
    grant = p.workflow_grant(wf.key)
    return {"status": "NEEDS_CLARIFICATION" if issues else "REVIEWABLE_REQUEST",
            "workflow": wf.key, "business_request": params, "issues": issues,
            "required_slots": [s.name for s in wf.slots if s.required],
            "authorized_to_begin": grant is not None and p.has("tx:begin"),
            "applied": False,
            "next_action": "BEGIN_AND_PROPOSE_EFFECTS" if not issues and grant is not None and p.has("tx:begin")
                           else "RESOLVE_ISSUES_OR_USE_AUTHORIZED_AGENT"}


@router.get("/explain/{tx_id}")
async def explain(tx_id: UUID, p: Principal = Depends(principal),
                  rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    decision = await rt.coordinator.evaluate_barrier(await rt.coordinator.root_of(p, tx_id))
    return {"advisory": True, "explanation": await rt.explainer.explain_decision(decision),
            "decision_eligible": decision.eligible}
