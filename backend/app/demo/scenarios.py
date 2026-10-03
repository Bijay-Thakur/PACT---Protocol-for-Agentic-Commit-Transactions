"""Reproducible demo scenarios driven entirely through the public PACT API.

Each run seeds a fresh simulated customer (deterministic data, unique id so runs
never collide), arms the scenario's fault injection in the simulated provider,
and lets the scripted agents propose -> prepare -> commit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.agents.client import HttpPactClient, PactApiError
from app.agents.scripted_demo_agents import RootAgent, agents_from_specs
from app.demo.specs import (
    budget_child_specs,
    budget_root_spec,
    child_specs,
    op_keys,
    root_spec,
)


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    description: str
    expected_state: str
    faults: list[dict[str, Any]] = field(default_factory=list)
    refund_amount: str = "143.27"
    billing_cap: str = "200.00"
    kind: str = "cancellation"


SCENARIOS: dict[str, Scenario] = {s.key: s for s in [
    Scenario("success", "A - Successful global commit",
             "All children prepare, the barrier passes, effects execute in DAG order and are independently verified.",
             "COMMITTED_VERIFIED"),
    Scenario("invariant-failure", "B - Pre-commit invariant violation",
             "The billing agent proposes a $450 refund (within its own $500 cap) but the unused period is worth "
             "$143.27. The barrier blocks before any external call.",
             "ABORTED", refund_amount="450.00", billing_cap="500.00"),
    Scenario("budget-conflict", "C - Cross-agent shared-budget conflict",
             "Three billing agents each propose a refund within their local $6,000 cap ($1,800 + $3,800 + $5,500). "
             "Together they exceed the $10,000 root authority; only the global barrier can see it.",
             "ABORTED", kind="budget"),
    Scenario("unknown", "D - Ambiguous result / UNKNOWN reconciliation",
             "Billing applies the refund but the response is lost. PACT marks the effect UNKNOWN, does not retry, "
             "reconciles by operation identity, finds the refund, and continues.",
             "COMMITTED_VERIFIED",
             faults=[{"system": "billing", "operation": "create_refund", "mode": "drop_response_after_apply"}]),
    Scenario("compensation", "E - Partial commit with compensation",
             "Subscription cancel and CRM update are verified, then the entitlement revoke is definitively rejected. "
             "PACT releases the never-dispatched refund and notification and compensates CRM and subscription in "
             "reverse order, verifying each compensation.",
             "COMPENSATED",
             faults=[{"system": "identity", "operation": "revoke", "mode": "reject",
                      "params": {"message": "entitlement locked by compliance hold"}}]),
    Scenario("compensation-failure", "E2 - Compensation that cannot fully succeed",
             "As E, but reactivating the subscription also fails. PACT does not claim a rollback: the transaction "
             "escalates to HUMAN_REQUIRED and the receipt shows exactly what remains committed.",
             "HUMAN_REQUIRED",
             faults=[{"system": "identity", "operation": "revoke", "mode": "reject"},
                     {"system": "subscription", "operation": "reactivate", "mode": "reject"}]),
    Scenario("notification-ordering", "F - Notification waits for verification",
             "The refund is accepted but stays invisible to readers for several polls (eventual consistency). "
             "The confirmation is not dispatched until the refund is verified.",
             "COMMITTED_VERIFIED",
             faults=[{"system": "billing", "operation": "create_refund", "mode": "delay_visibility",
                      "params": {"reads": 3}}]),
    Scenario("verification-mismatch", "G - Provider says success, reality disagrees",
             "The CRM returns 200 without applying the change. Verification catches it, the effect FAILS, and PACT "
             "compensates the cancellation.",
             "COMPENSATED",
             faults=[{"system": "crm", "operation": "update", "mode": "ghost_success"}]),
    Scenario("duplicate-operation", "H - Duplicate logical operation suppressed",
             "After a verified cancellation, another agent run proposes the same refund operation_key. The barrier "
             "blocks it (OPERATION_ALREADY_VERIFIED); exactly one refund exists.",
             "ABORTED", kind="duplicate"),
]}


@dataclass
class ScenarioOptions:
    background: bool = False
    step_delay_ms: int = 0
    pause_at_unknown: bool = False


async def run_scenario(key: str, pact: HttpPactClient, sim: httpx.AsyncClient, opts: ScenarioOptions) -> dict[str, Any]:
    sc = SCENARIOS[key]
    cid = f"C-48291-{uuid.uuid4().hex[:5].upper()}"
    profile = "budget" if sc.kind == "budget" else "standard"
    (await sim.post("/sim/seed", json={"customer_id": cid, "profile": profile})).raise_for_status()
    for f in sc.faults:
        (await sim.post("/sim/faults", json={**f, "customer_id": cid})).raise_for_status()

    root = RootAgent()
    if sc.kind == "budget":
        root_id = await root.open(pact, budget_root_spec(cid))
        await root.delegate(pact, root_id, agents_from_specs(budget_child_specs(cid)))
    else:
        root_id = await root.open(pact, root_spec(cid, auto_reconcile=not opts.pause_at_unknown, scenario=key,
                                                  amount_limit="500.00"))
        await root.delegate(pact, root_id, agents_from_specs(
            child_specs(cid, refund_amount=sc.refund_amount, billing_cap=sc.billing_cap)))

    background = opts.background and sc.kind != "duplicate"
    commit = await root.prepare_and_commit(pact, root_id, step_delay_ms=opts.step_delay_ms, background=background)
    expected = "UNKNOWN" if key == "unknown" and opts.pause_at_unknown else sc.expected_state
    result: dict[str, Any] = {
        "scenario": key, "title": sc.title, "customer_id": cid, "root_id": root_id,
        "expected_state": expected, "faults": sc.faults,
        "commit_decision": {"eligible": commit["decision"]["eligible"],
                            "blocking_reasons": commit["decision"]["blocking_reasons"]},
    }

    if sc.kind == "duplicate":
        first_state = commit["state"]
        k = op_keys(cid)
        base = root_spec(cid, scenario=key)
        dup_root = await root.open(pact, {
            **base, "required_effect_types": ["billing.refund"],
            "objective": f"(retry by another agent run) Refund unused period for {cid}",
            "invariants": [i for i in base["invariants"] if i["key"] in ("single_customer", "unique_operations")],
        })
        dup_child = [s for s in child_specs(cid) if s["actor_id"] == "billing_agent"][0]
        dup_child = {**dup_child, "effects": [{**dup_child["effects"][0], "depends_on": []}]}
        await root.delegate(pact, dup_root, agents_from_specs([dup_child]))
        dup_commit = await root.prepare_and_commit(pact, dup_root)
        result.update({
            "first_root_id": root_id, "first_state": first_state, "root_id": dup_root,
            "duplicate_operation_key": k["refund"],
            "commit_decision": {"eligible": dup_commit["decision"]["eligible"],
                                "blocking_reasons": dup_commit["decision"]["blocking_reasons"]},
        })
        root_id = dup_root

    try:
        result["state"] = (await pact.get(root_id))["transaction"]["state"]
    except PactApiError as exc:  # pragma: no cover - surfaced to caller
        result["state"] = f"ERROR {exc}"
    calls = (await sim.get("/sim/calls", params={"customer_id": cid})).json()
    result["provider_calls"] = [f"{c['system']}.{c['operation']}:{c['outcome']}" for c in calls]
    # In background mode the run is still in flight when this returns.
    result["matches_expected"] = None if background else result["state"] == expected
    return result
