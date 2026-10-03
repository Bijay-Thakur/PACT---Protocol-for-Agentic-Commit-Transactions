from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import runtime
from app.runtime import Runtime

router = APIRouter(prefix="/api/v1", tags=["contracts"])


@router.get("/contracts")
async def list_contracts(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """The Effect Contract Registry: how each effect behaves transactionally."""
    return {"contracts": [c.public() for c in rt.registry.contracts()]}
