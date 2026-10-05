"""A16: hierarchical resource claims use the documented cross-root compatibility matrix."""

from __future__ import annotations

import uuid

from app.core.reservations import ClaimRequest
from app.domain.effect import EffectProposal
from app.domain.enums import PrincipalKind
from app.domain.transaction import BeginRequest

from .conftest import requires_db


@requires_db
async def test_aggregate_subresource_read_write_compatibility(rt):
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    cid = f"C-RES-{uuid.uuid4().hex[:6].upper()}"
    agent = await rt.principals.upsert_principal(tenant, "agent", PrincipalKind.AGENT,
        ["tx:begin", "tx:propose"],
        {"workflows": {"customer_remediation": {"remediation_budget": "200.00"}}})
    ids = []
    for _ in range(4):
        root = await rt.manager.begin(agent, BeginRequest(workflow="customer_remediation",
            business_request={"customer_id": cid, "incident_id": f"INC-{uuid.uuid4().hex[:6].upper()}"}))
        effect, _ = await rt.manager.propose(agent, root, EffectProposal(
            effect_type="billing.refund", slot="refund_line",
            payload={"customer_id": cid, "charge_id": "ch_fixture", "amount": "1.00"}))
        ids.append((root, effect))
    aggregate = f"billing/customer:{cid}/refunds"
    child = f"{aggregate}/charge:ch_fixture"

    async def reserve(index: int, resource: str, mode: str):
        root, effect = ids[index]
        async with rt.db.uow() as s:
            return await rt.reservations.reserve(s, tenant, root,
                [ClaimRequest(effect_id=effect, resource=resource, mode=mode)])

    assert await reserve(0, aggregate, "READ") == []
    assert await reserve(1, child, "READ") == []
    conflict = await reserve(2, child, "WRITE")
    assert {c.held_by_root for c in conflict} == {ids[0][0], ids[1][0]}
    async with rt.db.uow() as s:
        await rt.reservations.release(s, ids[0][0], [], "test read completed")
        await rt.reservations.release(s, ids[1][0], [], "test read completed")
    assert await reserve(2, child, "WRITE") == []
    conflict = await reserve(3, aggregate, "EXCLUSIVE")
    assert len(conflict) == 1 and conflict[0].held_by_root == ids[2][0]
