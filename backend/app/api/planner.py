"""Model integration boundary. Proposals only - nothing here can commit."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from app.api.deps import principal, runtime
from app.agents.model_provider import PlanProposal
from app.domain.errors import ValidationFailed
from app.runtime import Runtime
from app.security.principals import Principal
from app.persistence.models import ProposalTraceRow

router = APIRouter(prefix="/api/v1/planner", tags=["planner"])


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
    spec = await rt.planner.propose_transaction(body.intent, body.context)
    live = rt.planner.name != "deterministic_fixture"
    async with rt.db.uow() as session:
        session.add(ProposalTraceRow(
            tenant_id=p.tenant_id, principal_id=p.id, provider=rt.planner.name,
            model=getattr(rt.planner, "model", None), live=live,
            request_id=getattr(rt.planner, "last_request_id", None),
            latency_ms=getattr(rt.planner, "last_latency_ms", None),
            prompt_version="intent-extract/1", schema_version="plan-proposal/1",
            intent_digest=hashlib.sha256(body.intent.encode()).hexdigest(),
            proposal=spec.model_dump(mode="json"), validation={"schema_valid": True},
            outcome="PROPOSED", usage=getattr(rt.planner, "last_usage", None) or {}))
    return {
        "provider": rt.planner.name,
        "live": live,
        "model": getattr(rt.planner, "model", None),
        "usage": getattr(rt.planner, "last_usage", None),
        "request_id": getattr(rt.planner, "last_request_id", None),
        "proposed_plan": spec.model_dump(mode="json"),
        "applied": False,
        "note": "Proposal only; a trusted workflow must resolve facts and compile policy before review.",
    }


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
