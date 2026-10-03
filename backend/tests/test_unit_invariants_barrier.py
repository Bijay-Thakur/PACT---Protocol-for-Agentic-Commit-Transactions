from datetime import timedelta

from app.adapters.registry import default_registry
from app.config import Settings
from app.core.authority_engine import AuthorityEngine
from app.core.commit_barrier import CommitBarrier
from app.core.effect_graph import EffectGraph
from app.core.invariant_engine import InvariantEngine
from app.domain.enums import EffectState, InvariantPhase, LogicalOperationStatus, TransactionState

from .factories import NOW, Tree

engine = InvariantEngine()
barrier = CommitBarrier(default_registry(), AuthorityEngine(Settings()), engine)


def evaluate(t: Tree, phase=InvariantPhase.PRE_COMMIT):
    snap = t.snapshot()
    return {e.invariant_key: e for e in engine.evaluate(snap, EffectGraph(snap.effects), phase, NOW)}


def refund_tree(amount: str, unused: str = "143.27", cap: str = "200") -> Tree:
    t = Tree()
    sub = t.child("subscription_agent", ["subscription.cancel"], ["customer:C1/*"])
    t.add(sub, "cancel", "subscription.cancel", payload={"customer_id": "C1"}, evidence={"unused_balance": unused},
          claims=[("customer:C1/subscription", "WRITE")])
    bill = t.child("billing_agent", ["billing.refund"], ["customer:C1/refund_balance"], cap)
    t.add(bill, "refund", "billing.refund", amount=amount, payload={"customer_id": "C1", "amount": amount},
          claims=[("customer:C1/refund_balance", "EXCLUSIVE")], deps=["cancel"])
    t.invariant(key="refund_ok", name="refund", phase="PRE_COMMIT", expression_type="effect_amount_lte",
                failure_action="BLOCK_COMMIT",
                config={"effect_type": "billing.refund",
                        "limit_refs": ["capability.amount_limit", "observation:subscription.cancel.unused_balance"]})
    return t


def test_effect_amount_uses_min_of_capability_and_observation():
    ok = evaluate(refund_tree("143.27"))["refund_ok"]
    assert ok.passed and ok.observed_values["effects"][0]["authorized"] == "143.27"
    bad = evaluate(refund_tree("150.00"))["refund_ok"]
    assert not bad.passed  # within the $200 cap but above the unused balance


def test_missing_observation_fails_closed():
    t = refund_tree("10.00")
    t.effects[0] = t.effects[0].__class__(**{**t.effects[0].__dict__, "prepare_evidence": {}})
    assert not evaluate(t)["refund_ok"].passed


def test_sum_and_field_and_unique_invariants():
    t = Tree({"amount": "10000", "cumulative": "10000"}, meta={"customer_id": "C1"})
    for actor, amt in (("a", "1800"), ("b", "3800"), ("c", "5500")):
        tx = t.child(actor, ["billing.refund"], ["customer:C1/*"], "6000")
        t.add(tx, f"k-{actor}", "billing.refund", amount=amt, payload={"customer_id": "C1" if actor != "c" else "C9"})
    t.invariant(key="budget", name="b", phase="PRE_COMMIT", expression_type="sum_amount_lte",
                failure_action="BLOCK_COMMIT", config={"limit_ref": "root.capability.cumulative_amount_limit"})
    t.invariant(key="same_customer", name="c", phase="PRE_COMMIT", expression_type="field_matches",
                failure_action="BLOCK_COMMIT", config={"field": "customer_id", "equals_ref": "metadata.customer_id"})
    t.invariant(key="unique", name="u", phase="PRE_COMMIT", expression_type="unique_operation_keys",
                failure_action="BLOCK_COMMIT")
    r = evaluate(t)
    assert not r["budget"].passed and r["budget"].observed_values["sum"] == "11100"
    assert not r["same_customer"].passed and r["same_customer"].observed_values["mismatched"] == ["k-c"]
    assert r["unique"].passed


def test_notification_ordering_invariant_pre_and_post():
    t = Tree()
    tx = t.child("n", ["notification.send", "billing.refund"], ["customer:C1/*"])
    refund = t.add(tx, "refund", "billing.refund", amount="1", verified_at=NOW + timedelta(seconds=5),
                   state=EffectState.VERIFIED)
    t.add(tx, "notify", "notification.send", deps=["refund"], dispatched_at=NOW + timedelta(seconds=9))
    for phase in ("PRE_COMMIT", "POST_EXECUTION"):
        t.invariant(key=f"gate_{phase.lower()}", name="g", phase=phase, expression_type="depends_on_verified",
                    failure_action="BLOCK_COMMIT", config={"effect_type": "notification.send", "requires": ["billing.refund"]})
    assert evaluate(t)["gate_pre_commit"].passed
    assert evaluate(t, InvariantPhase.POST_EXECUTION)["gate_post_execution"].passed
    # Dispatched before the refund was verified -> violation.
    t.effects[1] = t.effects[1].__class__(**{**t.effects[1].__dict__, "dispatched_at": refund.verified_at - timedelta(seconds=1)})
    assert not evaluate(t, InvariantPhase.POST_EXECUTION)["gate_post_execution"].passed


def test_barrier_eligible_snapshot():
    d = barrier.evaluate(refund_tree("143.27").snapshot(), NOW, binding=False).decision
    assert d.eligible, d.blocking_reasons
    codes = {c.code for c in d.checks}
    assert {"TRANSACTION_STATE_VALID", "CHILD_PREPARED", "AUTHORITY_VALID", "NO_AUTHORITY_ESCALATION",
            "GLOBAL_BUDGET", "RESOURCE_CLAIMS_COMPATIBLE", "INVARIANT", "IDEMPOTENCY", "APPROVALS_SATISFIED",
            "NO_BLOCKING_UNKNOWN", "DEPENDENCY_GRAPH_VALID", "EFFECT_CONTRACTS_RESOLVED",
            "REQUIRED_EFFECTS_PRESENT"} <= codes


def test_barrier_reports_every_blocking_reason():
    t = refund_tree("150.00")
    t.txs[t.root_id] = t.txs[t.root_id].__class__(**{**t.txs[t.root_id].__dict__,
                                                     "meta": {"customer_id": "C1", "requires_approval": True}})
    extra = t.child("late_agent", ["crm.update"], ["customer:C1/*"], state=TransactionState.PREPARING)
    t.add(extra, "crm", "crm.update", claims=[("customer:C1/crm_record", "WRITE")])
    d = barrier.evaluate(t.snapshot(), NOW, binding=True).decision
    assert not d.eligible
    assert "INVARIANT_FAILED:refund_ok" in d.blocking_reasons
    assert "CHILD_NOT_PREPARED:late_agent" in d.blocking_reasons
    assert "APPROVAL_REQUIRED" in d.blocking_reasons


def test_barrier_blocks_verified_or_foreign_operations_and_unknowns():
    t = refund_tree("100.00")
    bill_tx = t.effects[1].transaction_id
    t.add(bill_tx, "already-done", "billing.refund", amount="1", op_status=LogicalOperationStatus.VERIFIED,
          claims=[("customer:C1/refund_balance/x", "EXCLUSIVE")])
    t.add(bill_tx, "elsewhere", "billing.refund", amount="1", op_status=LogicalOperationStatus.IN_FLIGHT,
          claims=[("customer:C1/refund_balance/y", "EXCLUSIVE")])
    reasons = barrier.evaluate(t.snapshot(), NOW, binding=True).decision.blocking_reasons
    assert "OPERATION_ALREADY_VERIFIED:already-done" in reasons
    assert "OPERATION_IN_FLIGHT_ELSEWHERE:elsewhere" in reasons

    t2 = refund_tree("100.00")
    t2.add(t2.effects[1].transaction_id, "limbo", "billing.refund", amount="1", state=EffectState.UNKNOWN,
           claims=[("customer:C1/refund_balance/z", "EXCLUSIVE")])
    assert "UNRESOLVED_UNKNOWN" in barrier.evaluate(t2.snapshot(), NOW, binding=True).decision.blocking_reasons


def test_barrier_is_deterministic():
    snap = refund_tree("150.00").snapshot()
    a = barrier.evaluate(snap, NOW, binding=False).decision.model_dump(mode="json")
    b = barrier.evaluate(snap, NOW, binding=False).decision.model_dump(mode="json")
    assert a == b
