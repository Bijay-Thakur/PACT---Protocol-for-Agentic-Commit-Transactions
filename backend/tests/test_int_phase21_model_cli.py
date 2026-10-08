"""The owner CLI uses the same durable proposal boundary as the API."""
from __future__ import annotations

import json
import asyncio
import uuid
import os
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agents.model_provider import PlanProposal
from app.api.planner import AcceptRequest, ProposeRequest, accept, assemble, propose
from app.cli import _model_command
from app.domain.enums import PrincipalKind
from app.domain.errors import ValidationFailed
from app.persistence.models import ProposalTraceRow
from app.security.principals import Principal

from .conftest import requires_db

pytestmark = requires_db


async def test_fixture_smoke_creates_durable_proposal_trace(rt, monkeypatch, capsys):
    monkeypatch.setenv("PACT_DATABASE_URL", rt.settings.database_url)
    assert await _model_command("model-smoke", ["--profile", "deterministic_fixture",
                                                 "--max-calls", "1", "--tenant", "phase21-cli"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] == 1 and report["applied"] is False
    trace_id = report["results"][0]["proposal_trace_id"]
    async with rt.db.read() as session:
        rows = list((await session.execute(select(ProposalTraceRow).where(
            ProposalTraceRow.tenant_id == "phase21-cli"))).scalars())
    assert len(rows) == 1 and str(rows[0].id) == trace_id
    assert rows[0].outcome == "PROPOSED" and rows[0].provider == "deterministic_fixture"


async def test_model_admission_is_durable_across_connections_and_failure(rt, monkeypatch):
    class FakeLive:
        name = "groq_dev"
        model = "test-model"
        last_usage = None
        last_request_id = None
        last_latency_ms = None
        async def propose_transaction(self, intent, context):
            await asyncio.sleep(0.05)
            if "fail" in intent:
                raise ValidationFailed("provider failed", code="PLANNER_PROVIDER_ERROR")
            return PlanProposal(objective=intent, requested_workflow="customer_offboarding",
                                entity_references={"customer_id": "C-TEST"})

    monkeypatch.setattr(rt, "planner", FakeLive())
    monkeypatch.setattr(rt, "settings", replace(rt.settings, model_max_requests_per_hour=1,
                                                 model_max_inflight=1))
    tenant = f"phase21-model-{uuid.uuid4().hex[:8]}"
    principal = Principal(uuid.uuid4(), tenant, "test-model", PrincipalKind.SERVICE,
                          frozenset({"planner:propose"}))
    one, two = await asyncio.gather(
        propose(ProposeRequest(intent="Cancel customer C-TEST"), principal, rt),
        propose(ProposeRequest(intent="Cancel customer C-TEST"), principal, rt),
        return_exceptions=True)
    assert sum(isinstance(result, dict) for result in (one, two)) == 1
    assert sum(isinstance(result, ValidationFailed) and result.code == "PLANNER_BUDGET_EXHAUSTED"
               for result in (one, two)) == 1
    async with rt.db.read() as session:
        rows = list((await session.execute(select(ProposalTraceRow).where(
            ProposalTraceRow.tenant_id == tenant))).scalars())
    assert len(rows) == 1 and rows[0].outcome == "PROPOSED"
    fail_principal = Principal(uuid.uuid4(), tenant + "-failure", "test-model", PrincipalKind.SERVICE,
                               frozenset({"planner:propose"}))
    try:
        await propose(ProposeRequest(intent="fail customer C-TEST"), fail_principal, rt)
    except ValidationFailed as exc:
        assert exc.code == "PLANNER_PROVIDER_ERROR"
    else:
        raise AssertionError("model failure was not propagated")
    async with rt.db.read() as session:
        failed = (await session.execute(select(ProposalTraceRow).where(
            ProposalTraceRow.tenant_id == fail_principal.tenant_id))).scalar_one()
    assert failed.outcome == "FAILED" and failed.usage["disposition"] == "UNKNOWN"


async def test_model_customer_mismatch_needs_explicit_correction(rt, client, monkeypatch):
    class WrongEntity:
        name = "deterministic_fixture"
        async def propose_transaction(self, intent, context):
            return PlanProposal(objective=intent, requested_workflow="customer_offboarding",
                                entity_references={"customer_id": "C-WRONG"},
                                requested_parameters={"customer_id": "C-WRONG"})

    monkeypatch.setattr(rt, "planner", WrongEntity())
    tenant = f"phase21-{uuid.uuid4().hex[:8]}"
    principal = await rt.principals.upsert_principal(tenant, "requester", PrincipalKind.AGENT,
        ["tx:begin", "planner:propose"], {"workflows": {"customer_offboarding": {
            "amount_limit": "500"}}})
    headers = {"Authorization": f"Bearer {await rt.principals.issue_api_key(principal.id)}"}
    proposal = await client.post("/api/v1/planner/propose", headers=headers,
                                 json={"intent": "Cancel customer C-CORRECT"})
    assert proposal.status_code == 200
    assert proposal.json()["intent_issues"][0]["code"] == "CRITICAL_ENTITY_MISMATCH"
    body = {"proposal_trace_id": proposal.json()["proposal_trace_id"],
            "business_request": {"customer_id": "C-CORRECT"},
            "clarified_objective": "Cancel customer C-CORRECT", "clarification_note": ""}
    request_headers = {**headers, "X-PACT-Request-ID": uuid.uuid4().hex}
    blocked = await client.post("/api/v1/planner/accept", headers=request_headers, json=body)
    assert blocked.status_code == 422
    wrong_objective = await client.post("/api/v1/planner/accept", headers=request_headers,
                                      json={**body, "clarification_note": "Corrected customer identity",
                                            "resolved_issue_codes": ["CRITICAL_ENTITY_MISMATCH"],
                                            "clarified_objective": "Cancel customer C-WRONG"})
    assert wrong_objective.status_code == 422
    corrected = await client.post("/api/v1/planner/accept", headers=request_headers,
                                  json={**body, "clarification_note": "Corrected customer identity",
                                        "resolved_issue_codes": ["CRITICAL_ENTITY_MISMATCH"]})
    assert corrected.status_code == 200 and corrected.json()["state"] == "CREATED"


async def test_cancelled_and_crashed_model_attempts_keep_conservative_reservations(rt, monkeypatch):
    entered = asyncio.Event()

    class HangingLive:
        name = "groq_dev"
        model = "test-model"
        last_usage = None
        last_request_id = None
        last_latency_ms = None
        async def propose_transaction(self, intent, context):
            entered.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(rt, "planner", HangingLive())
    monkeypatch.setattr(rt, "settings", replace(rt.settings, model_max_requests_per_hour=1))
    tenant = f"phase21-cancel-{uuid.uuid4().hex[:8]}"
    principal = Principal(uuid.uuid4(), tenant, "test-model", PrincipalKind.SERVICE,
                          frozenset({"planner:propose"}))
    task = asyncio.create_task(propose(ProposeRequest(intent="Cancel customer C-TEST"), principal, rt))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with rt.db.read() as session:
        cancelled = (await session.execute(select(ProposalTraceRow).where(
            ProposalTraceRow.tenant_id == tenant))).scalar_one()
    assert cancelled.outcome == "CANCELLED" and cancelled.usage["disposition"] == "UNKNOWN"

    crashed_tenant = f"phase21-crash-{uuid.uuid4().hex[:8]}"
    async with rt.db.uow() as session:
        session.add(ProposalTraceRow(tenant_id=crashed_tenant, principal_id=principal.id,
            provider="groq_dev", model="test-model", live=True,
            prompt_version="intent-extract/1", schema_version="plan-proposal/1",
            intent_digest="0" * 64, proposal=None, validation={}, outcome="ADMITTED",
            usage={"reserved_tokens": 2600, "disposition": "UNKNOWN"},
            created_at=datetime.now(UTC) - timedelta(minutes=2)))
    crash_principal = Principal(principal.id, crashed_tenant, "test-model", PrincipalKind.SERVICE,
                                frozenset({"planner:propose"}))
    with pytest.raises(ValidationFailed) as error:
        await propose(ProposeRequest(intent="Cancel customer C-TEST"), crash_principal, rt)
    assert error.value.code == "PLANNER_BUDGET_EXHAUSTED"
    async with rt.db.read() as session:
        crashed = (await session.execute(select(ProposalTraceRow).where(
            ProposalTraceRow.tenant_id == crashed_tenant))).scalar_one()
    assert crashed.outcome == "UNKNOWN_USAGE" and crashed.usage["reserved_tokens"] == 2600


async def test_model_allowance_holds_across_independent_processes(rt):
    script = Path(__file__).resolve().parents[2] / "scripts" / "model_admission_probe.py"
    tenant = f"phase21-process-{uuid.uuid4().hex[:8]}"
    env = {**os.environ, "PACT_TEST_DATABASE_URL": rt.settings.database_url}
    processes = [await asyncio.create_subprocess_exec(sys.executable, str(script), tenant,
        env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        for _ in range(2)]
    outputs = await asyncio.gather(*(process.communicate() for process in processes))
    assert all(process.returncode == 0 for process in processes), outputs
    outcomes = sorted(json.loads(out.decode())["outcome"] for out, _ in outputs)
    assert outcomes == ["PLANNER_BUDGET_EXHAUSTED", "PROPOSED"]
    async with rt.db.read() as session:
        rows = list((await session.execute(select(ProposalTraceRow).where(
            ProposalTraceRow.tenant_id == tenant))).scalars())
    assert len(rows) == 1 and rows[0].outcome == "PROPOSED"


async def test_profile_switch_keeps_trace_provenance_and_trusted_compiler_path(rt, monkeypatch):
    tenant = f"phase21-switch-{uuid.uuid4().hex[:8]}"
    cid = f"C-SWITCH-{uuid.uuid4().hex[:6].upper()}"
    principal = await rt.principals.upsert_principal(tenant, "requester", PrincipalKind.AGENT,
        ["planner:propose", "tx:begin", "tx:propose", "tx:prepare"],
        {"workflows": {"customer_offboarding": {"amount_limit": "500"}}})
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()

    class FakeProfile:
        def __init__(self, name):
            self.name, self.model = name, "synthetic-wire"
            self.last_usage = {"total_tokens": 10}
            self.last_request_id = None
            self.last_latency_ms = 1
        async def propose_transaction(self, intent, context):
            return PlanProposal(objective=intent, requested_workflow="customer_offboarding",
                                entity_references={"customer_id": cid})

    traces = []
    for name in ("groq_dev", "nebius_nemotron"):
        monkeypatch.setattr(rt, "planner", FakeProfile(name))
        response = await propose(ProposeRequest(intent=f"Cancel customer {cid}"), principal, rt)
        assert response["intent_issues"] == []
        traces.append(response["proposal_trace_id"])
    async with rt.db.read() as session:
        stored = list((await session.execute(select(ProposalTraceRow).where(
            ProposalTraceRow.tenant_id == tenant).order_by(ProposalTraceRow.created_at))).scalars())
    assert [row.provider for row in stored] == ["groq_dev", "nebius_nemotron"]
    accepted = await accept(AcceptRequest(proposal_trace_id=uuid.UUID(traces[1]),
        business_request={"customer_id": cid}, clarified_objective=f"Cancel customer {cid}",
        clarification_note="Reviewed"), uuid.uuid4().hex, principal, rt)
    root = uuid.UUID(accepted["transaction_id"])
    assert (await assemble(root, principal, rt))["status"] == "ASSEMBLED"
    assert (await rt.coordinator.prepare(principal, root))["status"] == "FROZEN"
