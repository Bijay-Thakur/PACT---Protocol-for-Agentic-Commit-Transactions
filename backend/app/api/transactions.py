"""Transaction routes. Thin: validation in, domain services do the work, read model out."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import queries, runtime
from app.domain.effect import EffectProposal
from app.domain.enums import OperatorActionType
from app.domain.invariant import InvariantDefinition
from app.domain.transaction import ChildSpec, TransactionSpec
from app.runtime import Runtime
from app.services.query_service import QueryService

router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])


class CommitRequest(BaseModel):
    step_delay_ms: int = Field(default=0, ge=0, le=3000, description="Demo pacing between effect steps")
    background: bool = Field(default=False, description="Return after the barrier; execution continues async")


class OperatorActionRequest(BaseModel):
    operator_id: str = Field(min_length=1, max_length=128)
    action: OperatorActionType
    note: str = Field(default="", max_length=2000)
    effect_id: UUID | None = None


@router.post("", status_code=201)
async def create_transaction(spec: TransactionSpec, rt: Runtime = Depends(runtime),
                             q: QueryService = Depends(queries)) -> dict[str, Any]:
    tx_id = await rt.manager.create_root(spec)
    return await q.detail(tx_id)


@router.get("")
async def list_transactions(limit: int = Query(default=50, ge=1, le=500),
                            q: QueryService = Depends(queries)) -> dict[str, Any]:
    return {"transactions": await q.list_transactions(limit)}


@router.get("/{tx_id}")
async def get_transaction(tx_id: UUID, q: QueryService = Depends(queries)) -> dict[str, Any]:
    return await q.detail(tx_id)


@router.post("/{tx_id}/children", status_code=201)
async def create_child(tx_id: UUID, child: ChildSpec, rt: Runtime = Depends(runtime),
                       q: QueryService = Depends(queries)) -> dict[str, Any]:
    child_id = await rt.manager.create_child(tx_id, child)
    return await q.detail(child_id)


@router.post("/{tx_id}/effects", status_code=201)
async def propose_effect(tx_id: UUID, proposal: EffectProposal, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    effect_id = await rt.manager.propose_effect(tx_id, proposal)
    return {"effect_id": str(effect_id), "transaction_id": str(tx_id), "state": "VALIDATED",
            "note": "proposed only; nothing executes until the root crosses the global commit barrier"}


@router.post("/{tx_id}/invariants", status_code=201)
async def add_invariant(tx_id: UUID, inv: InvariantDefinition, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    await rt.manager.add_invariant(tx_id, inv)
    return {"transaction_id": str(tx_id), "invariant": inv.key}


@router.post("/{tx_id}/prepare")
async def prepare(tx_id: UUID, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    state = await rt.coordinator.prepare(tx_id)
    return {"transaction_id": str(tx_id), "state": state}


@router.get("/{tx_id}/commit-decision")
async def commit_decision(tx_id: UUID, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    root_id = await rt.coordinator._root_id(tx_id)
    decision = await rt.coordinator.evaluate_barrier(root_id)
    return {**decision.model_dump(mode="json"), "explanation": await rt.explainer.explain_decision(decision)}


@router.post("/{tx_id}/commit")
async def commit(tx_id: UUID, body: CommitRequest | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    body = body or CommitRequest()
    outcome = await rt.coordinator.commit(tx_id, step_delay_s=body.step_delay_ms / 1000, background=body.background)
    return {"transaction_id": str(tx_id), "state": outcome.state,
            "decision": outcome.decision.model_dump(mode="json"),
            "explanation": await rt.explainer.explain_decision(outcome.decision)}


@router.post("/{tx_id}/reconcile")
async def reconcile(tx_id: UUID, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"transaction_id": str(tx_id), "state": await rt.coordinator.reconcile(tx_id)}


@router.post("/{tx_id}/compensate")
async def compensate(tx_id: UUID, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"transaction_id": str(tx_id), "state": await rt.coordinator.compensate(tx_id)}


@router.post("/{tx_id}/operator-actions")
async def operator_action(tx_id: UUID, body: OperatorActionRequest, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    state = await rt.coordinator.operator_action(tx_id, body.operator_id, body.action, body.note, body.effect_id)
    return {"transaction_id": str(tx_id), "state": state}
