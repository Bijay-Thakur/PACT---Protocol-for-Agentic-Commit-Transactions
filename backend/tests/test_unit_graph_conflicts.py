from app.adapters.registry import default_registry
from app.core.conflict_manager import detect_conflicts
from app.core.effect_graph import EffectGraph
from app.domain.enums import EffectState

from .factories import Tree, effect

registry = default_registry()


def contract_of(t):
    return registry.contract(t) if registry.has(t) else None


def test_cycle_detection():
    g = EffectGraph([effect("a", deps=["c"]), effect("b", deps=["a"]), effect("c", deps=["b"])])
    v = g.validate()
    assert not v.valid and v.cycles


def test_unresolved_and_self_dependencies_invalidate_graph():
    v = EffectGraph([effect("a", deps=["missing"]), effect("b", deps=["b"])]).validate()
    assert set(v.unresolved) == {"a", "b"}


def test_topological_order_is_deterministic_and_respects_dependencies():
    effects = [effect("notify", deps=["refund", "cancel"]), effect("refund", deps=["cancel"]),
               effect("crm", deps=["cancel"]), effect("cancel")]
    order = [e.operation_key for e in EffectGraph(effects).topological_order()]
    assert order == ["cancel", "crm", "refund", "notify"]
    assert order == [e.operation_key for e in EffectGraph(list(reversed(effects))).topological_order()]
    levels = {EffectGraph(effects).effects[k].operation_key: v for k, v in EffectGraph(effects).levels().items()}
    assert levels == {"cancel": 0, "crm": 1, "refund": 1, "notify": 2}


def test_readiness_requires_verified_not_merely_dispatched():
    cancel = effect("cancel", state=EffectState.DISPATCHED)
    notify = effect("notify", deps=["cancel"])
    g = EffectGraph([cancel, notify])
    assert not g.is_ready(notify.id)
    g2 = EffectGraph([effect("cancel", state=EffectState.VERIFIED), notify])
    assert g2.is_ready(notify.id)


def test_reverse_order_for_compensation():
    effects = [effect("a"), effect("b", deps=["a"]), effect("c", deps=["b"])]
    assert [e.operation_key for e in EffectGraph(effects).reverse_topological_order()] == ["c", "b", "a"]


def _conflicts(tree):
    snap = tree.snapshot()
    return detect_conflicts(snap, EffectGraph(snap.effects), contract_of)


def test_unordered_writes_conflict_but_ordered_writes_do_not():
    t = Tree()
    tx = t.child("crm_agent", ["crm.update"], ["customer:C1/*"])
    t.add(tx, "w1", "crm.update", claims=[("customer:C1/crm_record", "WRITE")])
    t.add(tx, "w2", "crm.update", claims=[("customer:C1/crm_record", "WRITE")])
    assert [c.code for c in _conflicts(t)] == ["RESOURCE_WRITE_CONFLICT"]

    t2 = Tree()
    tx = t2.child("crm_agent", ["crm.update"], ["customer:C1/*"])
    t2.add(tx, "w1", "crm.update", claims=[("customer:C1/crm_record", "WRITE")])
    t2.add(tx, "w2", "crm.update", claims=[("customer:C1/crm_record", "WRITE")], deps=["w1"])
    assert _conflicts(t2) == []


def test_exclusive_conflicts_even_when_ordered_and_reads_are_compatible():
    t = Tree()
    tx = t.child("a", ["billing.refund", "notification.send"], ["customer:C1/*"])
    t.add(tx, "r1", "billing.refund", claims=[("customer:C1/refund_balance", "EXCLUSIVE")])
    t.add(tx, "r2", "notification.send", claims=[("customer:C1/refund_balance", "READ")], deps=["r1"])
    assert [c.code for c in _conflicts(t)] == ["RESOURCE_EXCLUSIVE_CONFLICT"]
    t2 = Tree()
    tx = t2.child("a", ["crm.update"], ["customer:C1/*"])
    t2.add(tx, "x", "crm.update", claims=[("customer:C1/crm_record", "READ")])
    t2.add(tx, "y", "crm.update", claims=[("customer:C1/crm_record", "READ")])
    assert _conflicts(t2) == []


def test_contradictory_effects_and_duplicates_across_agents():
    t = Tree()
    a = t.child("identity_agent", ["identity.revoke"], ["customer:C1/*"])
    b = t.child("other_agent", ["identity.grant"], ["customer:C1/*"])
    t.add(a, "revoke", "identity.revoke", claims=[("customer:C1/entitlement", "WRITE")])
    t.add(b, "grant", "identity.grant", claims=[("customer:C1/entitlement", "WRITE")], deps=["revoke"])
    assert [c.code for c in _conflicts(t)] == ["CONTRADICTORY_EFFECTS"]

    t2 = Tree()
    a = t2.child("billing_a", ["billing.refund"], ["customer:C1/*"])
    b = t2.child("billing_b", ["billing.refund"], ["customer:C1/*"])
    t2.add(a, "same-op", "billing.refund", claims=[("customer:C1/x", "READ")])
    t2.add(b, "same-op", "billing.refund", claims=[("customer:C1/x", "READ")])
    assert "DUPLICATE_OPERATION_KEY" in [c.code for c in _conflicts(t2)]
