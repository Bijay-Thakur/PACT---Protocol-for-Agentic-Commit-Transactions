from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from uuid import UUID
from sqlalchemy import select
from app.persistence.models import TransactionRow, CapabilityRow
from app.domain.errors import NotFound

from app.api.deps import principal, runtime
from app.runtime import Runtime
from app.security.principals import Principal

router = APIRouter(prefix="/api/v1", tags=["contracts"])


@router.get("/contracts")
async def list_contracts(transaction_id: UUID | None = None,
                         p: Principal = Depends(principal), rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """The Effect Contract Registry: how each effect behaves transactionally."""
    allowed = {slot.effect_type for wf in rt.workflows.all()
               if p.workflow_grant(wf.key) is not None for slot in wf.slots}
    if transaction_id is not None:
        async with rt.db.read() as s:
            tx = await s.get(TransactionRow, transaction_id)
            if tx is None or tx.tenant_id != p.tenant_id or tx.principal_id != p.id:
                raise NotFound("delegated transaction unavailable", code="TRANSACTION_NOT_FOUND")
            cap = await s.get(CapabilityRow, tx.capability_id)
            allowed |= set((cap.scope or {}).get("allowed_effect_types", []))
    return {"contracts": [c.public() for c in rt.registry.contracts() if c.effect_type in allowed]}


@router.get("/workflows")
async def list_workflows(transaction_id: UUID | None = None,
                         p: Principal = Depends(principal), rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    keys = {wf.key for wf in rt.workflows.all() if p.has("tx:begin") and p.workflow_grant(wf.key) is not None}
    if transaction_id is not None:
        async with rt.db.read() as s:
            tx = await s.get(TransactionRow, transaction_id)
            if tx is None or tx.tenant_id != p.tenant_id or tx.principal_id != p.id:
                raise NotFound("delegated transaction unavailable", code="TRANSACTION_NOT_FOUND")
            if tx.workflow:
                keys.add(tx.workflow)
    return {"workflows": [{**rt.workflows.get(k).describe(), "may_begin":
                           p.has("tx:begin") and p.workflow_grant(k) is not None} for k in sorted(keys)]}
