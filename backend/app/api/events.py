"""Durable transaction event history + a Server-Sent Events stream.

The stream tails ``transaction_events`` from PostgreSQL, so it works across
processes and survives restarts - it is a view of durable history, not an
in-memory pub/sub.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.api.deps import principal, queries
from app.security.principals import Principal
from app.services.query_service import QueryService

router = APIRouter(prefix="/api/v1/transactions", tags=["events"])


@router.get("/{tx_id}/events")
async def list_events(tx_id: UUID, after: int = Query(default=0, ge=0), limit: int = Query(default=500, le=2000),
                      scope: str = Query(default="tree", pattern="^(tree|transaction)$"),
                      p: Principal = Depends(principal),
                      q: QueryService = Depends(queries)) -> dict[str, Any]:
    return {"events": await q.events(p, tx_id, after=after, limit=limit, scope=scope)}


@router.get("/{tx_id}/events/stream")
async def stream_events(tx_id: UUID, request: Request, after: int = Query(default=0, ge=0),
                        p: Principal = Depends(principal),
                        q: QueryService = Depends(queries)) -> StreamingResponse:
    await q.events(p, tx_id, after=0, limit=1)  # 404 early if unknown

    async def gen():
        cursor = after
        idle = 0
        while not await request.is_disconnected():
            batch = await q.events(p, tx_id, after=cursor, limit=200)
            for ev in batch:
                cursor = ev["sequence"]
                yield f"id: {cursor}\nevent: pact\ndata: {json.dumps(ev)}\n\n"
            idle = 0 if batch else idle + 1
            if idle and idle % 30 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
