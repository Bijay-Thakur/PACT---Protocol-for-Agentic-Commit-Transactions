"""Model integration boundary. Proposals only - nothing here can commit."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import runtime
from app.runtime import Runtime

router = APIRouter(prefix="/api/v1/planner", tags=["planner"])


class ProposeRequest(BaseModel):
    intent: str = Field(min_length=3, max_length=2000)
    context: dict[str, Any] = Field(default_factory=dict)


@router.post("/propose")
async def propose(body: ProposeRequest, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    spec = await rt.planner.propose_transaction(body.intent, body.context)
    return {
        "provider": rt.planner.name,
        "proposed_spec": spec.model_dump(mode="json"),
        "note": "Proposal only. Submit to POST /api/v1/transactions; PACT then validates issuer policy, "
                "delegation, payload schemas, invariants and the global commit barrier deterministically.",
    }


@router.get("/explain/{tx_id}")
async def explain(tx_id: UUID, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    decision = await rt.coordinator.evaluate_barrier(await rt.coordinator._root_id(tx_id))
    return {"advisory": True, "explanation": await rt.explainer.explain_decision(decision),
            "decision_eligible": decision.eligible}
