"""Authenticated transaction routes; all mutations enter shared application services."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query

from app.api.deps import principal, queries, runtime
from app.domain.effect import EffectProposal
from app.domain.transaction import (
    AbortRequest, ApprovalRequest, BeginRequest, CommitRequest, DelegateRequest,
    OperatorActionRequest, ReviseRequest, WithdrawRequest,
)
from app.runtime import Runtime
from app.security.principals import Principal
from app.services.query_service import QueryService

router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])


@router.post("", status_code=201)
async def create_transaction(body: BeginRequest, p: Principal = Depends(principal),
                             rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    tx_id = await rt.manager.begin(p, body)
    return {"transaction_id": str(tx_id), "state": "CREATED", "revision": 1}


@router.get("")
async def list_transactions(limit: int = Query(default=50, ge=1, le=500),
                            p: Principal = Depends(principal),
                            q: QueryService = Depends(queries)) -> dict[str, Any]:
    return {"transactions": await q.list_transactions(p, limit)}


@router.get("/{tx_id}")
async def get_transaction(tx_id: UUID, p: Principal = Depends(principal),
                          q: QueryService = Depends(queries)) -> dict[str, Any]:
    return await q.detail(p, tx_id)


@router.post("/{tx_id}/children", status_code=201)
async def create_child(tx_id: UUID, body: DelegateRequest, p: Principal = Depends(principal),
                       rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    child_id = await rt.manager.delegate(p, tx_id, body)
    return {"transaction_id": str(child_id), "root_id": str(tx_id), "state": "CREATED"}


@router.post("/{tx_id}/effects", status_code=201)
async def propose_effect(tx_id: UUID, body: EffectProposal, p: Principal = Depends(principal),
                         request_id: str | None = Header(default=None, alias="X-PACT-Request-ID"),
                         rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    effect_id, revision = await rt.manager.propose(p, tx_id, body, request_id=request_id)
    return {"effect_id": str(effect_id), "transaction_id": str(tx_id),
            "draft_revision": revision, "state": "VALIDATED", "applied": False}


@router.post("/{tx_id}/effects/{effect_id}/withdraw")
async def withdraw(tx_id: UUID, effect_id: UUID, body: WithdrawRequest,
                   p: Principal = Depends(principal), rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    await rt.manager.withdraw(p, tx_id, effect_id, body.reason)
    return {"effect_id": str(effect_id), "state": "ABORTED"}


@router.post("/{tx_id}/prepare")
async def prepare(tx_id: UUID, p: Principal = Depends(principal),
                  rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return await rt.coordinator.prepare(p, tx_id)


@router.post("/{tx_id}/revise")
async def revise(tx_id: UUID, body: ReviseRequest, p: Principal = Depends(principal),
                 rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return await rt.coordinator.revise(p, tx_id, body.reason)


@router.post("/{tx_id}/approve")
async def approve(tx_id: UUID, body: ApprovalRequest, p: Principal = Depends(principal),
                  rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return await rt.coordinator.approve(p, tx_id, body)


@router.get("/{tx_id}/commit-decision")
async def commit_decision(tx_id: UUID, p: Principal = Depends(principal),
                          rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    root_id = await rt.coordinator.root_of(p, tx_id)
    return (await rt.coordinator.evaluate_barrier(root_id)).model_dump(mode="json")


@router.post("/{tx_id}/commit")
async def commit(tx_id: UUID, body: CommitRequest, p: Principal = Depends(principal),
                 rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return await rt.coordinator.request_commit(p, tx_id, body)


@router.post("/{tx_id}/abort")
async def abort(tx_id: UUID, body: AbortRequest, p: Principal = Depends(principal),
                rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return await rt.coordinator.abort(p, tx_id, body.reason)


@router.post("/{tx_id}/operator-actions")
async def operator_action(tx_id: UUID, body: OperatorActionRequest,
                          p: Principal = Depends(principal), rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return await rt.coordinator.operator_action(p, tx_id, body)
