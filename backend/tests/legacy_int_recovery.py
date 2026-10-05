"""Restart recovery: in-flight state survives a coordinator crash and resumes from PostgreSQL."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import select

from app.persistence.models import EffectRow, OperationAttemptRow
from app.runtime import Runtime

from .conftest import BACKEND, TEST_DB, build_prepared, detail, event_types, external, make_settings, provider_calls, requires_db

pytestmark = requires_db


class SimulatedCrash(BaseException):
    """BaseException so no application handler can swallow it - like a process kill."""


async def test_crash_after_external_side_effect_then_restart(client, rt):
    cid = f"C-CRASH-{uuid.uuid4().hex[:4].upper()}"
    root_id = uuid.UUID(await build_prepared(client, rt, cid))

    async def crash_hook(point: str, effect_id: uuid.UUID) -> None:
        async with crashing.db.read() as s:
            eff = await s.get(EffectRow, effect_id)
        if point == "after_external_call" and eff.contract_type == "billing.refund":
            raise SimulatedCrash()

    crashing = Runtime(make_settings(), crash_hook=crash_hook, pool_size=2)
    await crashing.start()
    with pytest.raises(SimulatedCrash):
        await crashing.coordinator.commit(root_id)
    await crashing.stop()  # the "process" is gone; nothing survives but Postgres

    d = await detail(client, str(root_id))
    refund = next(e for e in d["effects"] if e["effect_type"] == "billing.refund")
    assert d["transaction"]["state"] == "COMMITTING"
    assert refund["state"] == "DISPATCHING"  # intent was persisted before the call
    assert refund["attempts"][0]["status"] == "INTENT_RECORDED"
    assert len((await external(rt, cid))["billing"]["refunds"]) == 1  # ...and the provider applied it

    fresh = Runtime(make_settings(), pool_size=2)
    await fresh.start()
    try:
        state = await fresh.coordinator.recover_root(root_id)
    finally:
        await fresh.stop()
    assert state == "COMMITTED_VERIFIED"
    d = await detail(client, str(root_id))
    refund = next(e for e in d["effects"] if e["effect_type"] == "billing.refund")
    assert [(a["kind"], a["status"]) for a in refund["attempts"]] == [
        ("EXECUTE", "ABANDONED_BY_RESTART"), ("RECONCILE", "FOUND_APPLIED")]
    assert "RESTART_RECOVERY" in await event_types(client, str(root_id))
    assert len(await provider_calls(rt, cid, "billing", "create_refund")) == 1
    assert len((await external(rt, cid))["billing"]["refunds"]) == 1


async def test_resume_inflight_discovers_work_from_postgres(client, rt):
    cid = f"C-RESUME-{uuid.uuid4().hex[:4].upper()}"
    root_id = uuid.UUID(await build_prepared(client, rt, cid))

    async def crash_hook(point: str, effect_id: uuid.UUID) -> None:
        raise SimulatedCrash()  # crash on the very first dispatch

    crashing = Runtime(make_settings(), crash_hook=crash_hook, pool_size=2)
    await crashing.start()
    with pytest.raises(SimulatedCrash):
        await crashing.coordinator.commit(root_id)
    await crashing.stop()

    fresh = Runtime(make_settings(), pool_size=2)
    await fresh.start()
    try:
        resumed = await fresh.coordinator.resume_inflight()
    finally:
        await fresh.stop()
    assert root_id in resumed
    assert (await detail(client, str(root_id)))["transaction"]["state"] == "COMMITTED_VERIFIED"
    assert len(await provider_calls(rt, cid, "subscription", "cancel")) == 1


@pytest.mark.skipif(sys.platform == "emscripten", reason="needs subprocess")
async def test_real_process_death_and_recovery(rt):
    """os._exit() in a child process right after billing applies the refund; a new process recovers."""
    cid = f"C-KILL-{uuid.uuid4().hex[:4].upper()}"
    env = {**os.environ, "PACT_DATABASE_URL": TEST_DB, "PACT_CRASH_CUSTOMER": cid,
           "PACT_AUTO_RECOVER_ON_STARTUP": "false", "PACT_ADAPTER_TIMEOUT_S": "0.5", "PACT_LOG_LEVEL": "WARNING"}
    crash = subprocess.run([sys.executable, "-m", "app.cli", "crash-midflight"], cwd=BACKEND, env=env,
                           capture_output=True, text=True, timeout=120)
    assert crash.returncode == 137, crash.stdout + crash.stderr
    lines = [json.loads(line) for line in crash.stdout.splitlines() if line.startswith("{")]
    root_id = next(line["root_id"] for line in lines if line["event"] == "ROOT_CREATED")
    assert any(line["event"] == "SIMULATED_PROCESS_CRASH" for line in lines)

    recover = subprocess.run([sys.executable, "-m", "app.cli", "recover"], cwd=BACKEND, env=env,
                             capture_output=True, text=True, timeout=120)
    assert recover.returncode == 0, recover.stdout + recover.stderr
    report = json.loads([line for line in recover.stdout.splitlines() if line.startswith("{")][-1])
    resumed = {r["root_id"]: r["state"] for r in report["resumed"]}
    assert resumed[root_id] == "COMMITTED_VERIFIED"
    assert len(await provider_calls(rt, cid, "billing", "create_refund")) == 1
    async with rt.db.read() as s:
        attempts = list((await s.execute(
            select(OperationAttemptRow.kind, OperationAttemptRow.status)
            .join(EffectRow, EffectRow.id == OperationAttemptRow.effect_id)
            .where(EffectRow.root_id == uuid.UUID(root_id), EffectRow.contract_type == "billing.refund")
            .order_by(OperationAttemptRow.started_at))).all())
    assert attempts == [("EXECUTE", "ABANDONED_BY_RESTART"), ("RECONCILE", "FOUND_APPLIED")]
