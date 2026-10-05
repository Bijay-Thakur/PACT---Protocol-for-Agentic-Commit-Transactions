"""Opt-in simulator harness using the same authenticated application services."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import principal, runtime
from app.core.state_machine import TERMINAL_TX_STATES
from app.demo.scenarios import SCENARIOS
from app.domain.capability import CapabilitySpec
from app.domain.effect import EffectProposal
from app.domain.enums import PrincipalKind, TransactionState
from app.domain.errors import NotFound, ValidationFailed
from app.domain.transaction import BeginRequest, CommitRequest, DelegateRequest, RecoveryPolicy
from app.persistence.models import TransactionRow
from app.runtime import Runtime
from app.security.principals import Principal
from app.worker import Worker

router = APIRouter(prefix="/api/v1/demo", tags=["demo"])


class RunRequest(BaseModel):
    background: bool = False
    step_delay_ms: int = Field(default=0, ge=0, le=3000)
    pause_at_unknown: bool = False


def _enabled(rt: Runtime) -> None:
    if not rt.settings.demo_mode or rt.sim_store is None:
        raise ValidationFailed("simulator demo is disabled", code="DEMO_DISABLED")


@router.get("/scenarios")
async def scenarios(p: Principal = Depends(principal), rt: Runtime = Depends(runtime)) -> dict:
    _enabled(rt)
    p.require("demo:run")
    return {"scenarios": [{"key": s.key, "title": s.title, "description": s.description,
                           "expected_state": s.expected_state, "faults": s.faults} for s in SCENARIOS.values()]}


async def _agent(rt: Runtime, tenant: str, name: str, grant: dict | None = None) -> Principal:
    scopes = ["tx:begin", "tx:delegate", "tx:propose", "tx:prepare", "tx:commit", "tx:abort"]
    return await rt.principals.upsert_principal(tenant, name, PrincipalKind.AGENT, scopes, grant or {})


async def _make_offboarding(rt: Runtime, tenant: str, cid: str, amount: str,
                            pause: bool) -> tuple[Principal, UUID]:
    names = ["demo_subscription", "demo_identity", "demo_crm", "demo_billing", "demo_notification"]
    actors = {name: await _agent(rt, tenant, name) for name in names}
    root_actor = await _agent(rt, tenant, "demo_offboarding_root", {"workflows": {"customer_offboarding": {
        "amount_limit": "500.00", "cumulative_amount_limit": "500.00",
        "approval_threshold": "500.00", "may_delegate_to": names,
        "delegation_depth": 1}}})
    root = await rt.manager.begin(root_actor, BeginRequest(
        workflow="customer_offboarding", business_request={"customer_id": cid},
        recovery_policy=RecoveryPolicy(auto_reconcile=not pause)))
    proposals = [
        ("demo_subscription", "cancel_subscription", "subscription.cancel", {"customer_id": cid}),
        ("demo_identity", "revoke_premium", "identity.revoke", {"customer_id": cid}),
        ("demo_crm", "mark_churned", "crm.update", {"customer_id": cid,
                                                      "lifecycle_state": "churned", "note": "Cancelled via PACT"}),
        ("demo_billing", "refund_unused", "billing.refund", {"customer_id": cid, "amount": amount}),
        ("demo_notification", "confirm_customer", "notification.send", {"customer_id": cid,
                                                                            "template": "cancellation_confirmation"}),
    ]
    for actor_name, slot, effect_type, payload in proposals:
        provider = effect_type.split(".")[0]
        child = await rt.manager.delegate(root_actor, root, DelegateRequest(
            recipient=actor_name, objective=slot,
            capability=CapabilitySpec(allowed_effect_types=[effect_type],
                allowed_resources=[f"{provider}/customer:{cid}/*"],
                amount_limit="500.00", cumulative_amount_limit="500.00")))
        await rt.manager.propose(actors[actor_name], child, EffectProposal(
            effect_type=effect_type, slot=slot, payload=payload))
    return root_actor, root


async def _make_budget(rt: Runtime, tenant: str, cid: str) -> tuple[Principal, UUID]:
    names = ["demo_platform", "demo_support", "demo_infra"]
    actors = {name: await _agent(rt, tenant, name) for name in names}
    root_actor = await _agent(rt, tenant, "demo_remediation_root", {"workflows": {"customer_remediation": {
        "remediation_budget": "10000.00", "may_delegate_to": names, "delegation_depth": 1}}})
    root = await rt.manager.begin(root_actor, BeginRequest(workflow="customer_remediation",
        business_request={"customer_id": cid, "incident_id": f"INC-{uuid.uuid4().hex[:8].upper()}"}))
    for name, amount in zip(names, ["1800.00", "3800.00", "5500.00"]):
        line = name.removeprefix("demo_")
        child = await rt.manager.delegate(root_actor, root, DelegateRequest(recipient=name,
            objective=f"Refund {line} line", capability=CapabilitySpec(
                allowed_effect_types=["billing.refund"], allowed_resources=[f"billing/customer:{cid}/*"],
                amount_limit="6000.00", cumulative_amount_limit="6000.00")))
        await rt.manager.propose(actors[name], child, EffectProposal(effect_type="billing.refund",
            slot="refund_line", payload={"customer_id": cid, "amount": amount,
                                          "charge_id": f"ch_{cid}_{line}"}))
    return root_actor, root


@router.post("/run/{scenario}")
async def run(scenario: str, body: RunRequest | None = None, p: Principal = Depends(principal),
              rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    _enabled(rt)
    p.require("demo:run")
    if scenario not in SCENARIOS:
        raise HTTPException(404, "unknown scenario")
    opts = body or RunRequest()
    sc = SCENARIOS[scenario]
    cid = f"C-DEMO-{uuid.uuid4().hex[:8].upper()}"
    profile = "budget" if scenario == "budget-conflict" else "standard"
    (await rt.http.post("/sim/seed", json={"customer_id": cid, "profile": profile})).raise_for_status()
    for fault in sc.faults:
        (await rt.http.post("/sim/faults", json={**fault, "customer_id": cid})).raise_for_status()
    if scenario == "budget-conflict":
        actor, root = await _make_budget(rt, p.tenant_id, cid)
    else:
        actor, root = await _make_offboarding(rt, p.tenant_id, cid, sc.refund_amount,
                                             opts.pause_at_unknown)
    frozen = await rt.coordinator.prepare(actor, root)
    if frozen["status"] != "FROZEN":
        await rt.coordinator.abort(actor, root, "simulator plan rejected before execution")
        decision = {"eligible": False, "blocking_reasons": [i["code"] for i in frozen.get("issues", [])]}
    else:
        outcome = await rt.coordinator.request_commit(actor, root, CommitRequest(
            revision_digest=frozen["digest"], step_delay_ms=opts.step_delay_ms))
        decision = outcome.get("decision", {"eligible": False, "blocking_reasons": [outcome["status"]]})
        if outcome["status"] == "QUEUED" and not opts.background:
            worker = Worker(rt)
            for _ in range(120):
                await worker.run_once(root)
                async with rt.db.read() as session:
                    state = (await session.get(TransactionRow, root)).state
                if TransactionState(state) in TERMINAL_TX_STATES or state == "HUMAN_REQUIRED" or (
                    state == "UNKNOWN" and opts.pause_at_unknown):
                    break
                await asyncio.sleep(0.05)
    first_root = str(root)
    if scenario == "duplicate-operation" and state == "COMMITTED_VERIFIED":
        actor, root = await _make_offboarding(rt, p.tenant_id, cid, sc.refund_amount, False)
        duplicate = await rt.coordinator.prepare(actor, root)
        if duplicate["status"] == "FROZEN":
            blocked = await rt.coordinator.request_commit(actor, root, CommitRequest(
                revision_digest=duplicate["digest"]))
            decision = blocked.get("decision", {"eligible": False, "blocking_reasons": [blocked["status"]]})
        else:
            decision = {"eligible": False, "blocking_reasons": [i["code"] for i in duplicate.get("issues", [])]}
        await rt.coordinator.abort(actor, root, "duplicate business operation blocked")
        state = "ABORTED"
    return {"scenario": scenario, "title": sc.title, "customer_id": cid,
            "root_id": str(root), "expected_state": sc.expected_state, "faults": sc.faults,
            "commit_decision": decision, "state": state if 'state' in locals() else
            ("QUEUED" if frozen["status"] == "FROZEN" else "ABORTED"),
            "first_root_id": first_root if scenario == "duplicate-operation" else None,
            "provenance": "SIMULATED"}


@router.get("/external-state/{tx_id}")
async def external_state(tx_id: UUID, p: Principal = Depends(principal),
                         rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    _enabled(rt)
    async with rt.db.read() as session:
        tx = await session.get(TransactionRow, tx_id)
        if tx is None:
            raise NotFound("transaction not found")
        await rt.manager.assert_can_read(session, p, tx.root_id)
        root = await session.get(TransactionRow, tx.root_id)
        cid = (root.meta or {}).get("customer_id")
    state = (await rt.http.get(f"/sim/state/{cid}")).json()
    calls = (await rt.http.get("/sim/calls", params={"customer_id": cid})).json()
    return {"state": state, "provider_calls": calls}
