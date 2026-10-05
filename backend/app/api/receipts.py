"""Receipt routes. Read-only by design: there is no route that can mutate a receipt."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import principal, queries
from app.security.principals import Principal
from app.services.query_service import QueryService

router = APIRouter(prefix="/api/v1/transactions", tags=["receipts"])


@router.get("/{tx_id}/receipt")
async def get_receipt(tx_id: UUID, p: Principal = Depends(principal),
                      q: QueryService = Depends(queries)) -> dict[str, Any]:
    return await q.receipt(p, tx_id)


@router.get("/{tx_id}/receipt/verify")
async def verify_receipt(tx_id: UUID, p: Principal = Depends(principal),
                         q: QueryService = Depends(queries)) -> dict[str, Any]:
    return await q.verify_receipt(p, tx_id)
