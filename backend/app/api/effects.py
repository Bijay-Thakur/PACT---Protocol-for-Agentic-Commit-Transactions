from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import principal, runtime
from app.runtime import Runtime
from app.security.principals import Principal

router = APIRouter(prefix="/api/v1", tags=["contracts"])


@router.get("/contracts")
async def list_contracts(p: Principal = Depends(principal), rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """The Effect Contract Registry: how each effect behaves transactionally."""
    allowed = {slot.effect_type for wf in rt.workflows.all()
               if p.workflow_grant(wf.key) is not None for slot in wf.slots}
    return {"contracts": [c.public() for c in rt.registry.contracts() if c.effect_type in allowed]}
