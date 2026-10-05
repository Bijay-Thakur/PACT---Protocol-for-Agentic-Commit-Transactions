"""A22: stalled child preparation resets safely and discards an old provider read."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.api.demo import _make_offboarding
from app.persistence.models import EffectRow, TransactionRow

from .conftest import requires_db


@requires_db
async def test_sweeper_resets_stalled_child_prepare_and_stale_read_cannot_freeze(rt):
    cid = f"C-PREP-{uuid.uuid4().hex[:6].upper()}"
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    _, root = await _make_offboarding(rt, tenant, cid, "143.27", False)
    actor = await rt.principals.by_name(tenant, "demo_subscription")
    assert actor is not None
    async with rt.db.read() as session:
        child = (await session.execute(select(TransactionRow).where(
            TransactionRow.root_id == root, TransactionRow.principal_id == actor.id))).scalar_one()
        child_id = child.id
    adapter = rt.registry.adapter("subscription.cancel")
    original_prepare = adapter.prepare
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_prepare(effect, ctx):
        entered.set()
        await release.wait()
        return await original_prepare(effect, ctx)

    adapter.prepare = delayed_prepare
    try:
        pending = asyncio.create_task(rt.coordinator.prepare(actor, child_id))
        await asyncio.wait_for(entered.wait(), timeout=5)
        async with rt.db.uow() as session:
            await session.execute(update(TransactionRow).where(TransactionRow.id == child_id).values(
                updated_at=datetime.now(UTC) - timedelta(minutes=2)))
        repaired = await rt.coordinator.sweep(prepare_stall_s=60)
        assert str(child_id) in repaired["prepare_reset"]
        release.set()
        result = await pending
        assert result["state"] == "SPECIFYING"
    finally:
        release.set()
        adapter.prepare = original_prepare
    async with rt.db.read() as session:
        effect = (await session.execute(select(EffectRow).where(
            EffectRow.transaction_id == child_id))).scalar_one()
        assert effect.state == "VALIDATED"  # the delayed read did not freeze stale facts
    retried = await rt.coordinator.prepare(actor, child_id)
    assert retried["state"] == "PREPARED"
