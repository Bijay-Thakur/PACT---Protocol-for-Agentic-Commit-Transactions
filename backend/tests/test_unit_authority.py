from datetime import timedelta
from decimal import Decimal

import pytest

from app.config import Settings
from app.core.authority_engine import AuthorityEngine, pattern_covers
from app.domain.capability import CapabilitySpec, RootCapabilityGrant
from app.domain.enums import EffectState

from .factories import NOW, Tree, cap, effect

engine = AuthorityEngine(Settings())
ROOT = cap("root_agent", ["billing.refund", "crm.update"], ["customer:C1/*"], "500", "500", depth=2)


def spec(**kw) -> CapabilitySpec:
    base = {"allowed_effect_types": ["billing.refund"], "allowed_resources": ["customer:C1/refund_balance"],
            "amount_limit": "200", "cumulative_amount_limit": "200", "delegation_depth": 0}
    base.update(kw)
    return CapabilitySpec(**base)


@pytest.mark.parametrize("parent,child,ok", [
    ("*", "anything/at/all", True),
    ("customer:C1/*", "customer:C1/refund_balance", True),
    ("customer:C1/*", "customer:C1/refund_balance/*", True),
    ("customer:C1/*", "customer:C2/refund_balance", False),
    ("customer:C1/refund_balance", "customer:C1/*", False),
    ("customer:C1/refund_balance", "customer:C1/refund_balance", True),
])
def test_pattern_subset(parent, child, ok):
    assert pattern_covers(parent, child) is ok


def test_valid_delegation_is_a_subset():
    assert engine.validate_delegation(ROOT, spec(), NOW) == []


@pytest.mark.parametrize("kw,code", [
    ({"allowed_resources": ["customer:*"]}, "RESOURCE_SCOPE_BROADENED"),
    ({"allowed_effect_types": ["billing.refund", "identity.revoke"]}, "EFFECT_TYPE_EXPANSION"),
    ({"amount_limit": "600"}, "AMOUNT_ESCALATION"),
    ({"amount_limit": None}, "AMOUNT_ESCALATION"),  # unbounded child of a bounded parent
    ({"cumulative_amount_limit": "501"}, "AMOUNT_ESCALATION"),
    ({"delegation_depth": 2}, "DELEGATION_DEPTH_EXCEEDED"),
])
def test_escalating_delegations_are_rejected(kw, code):
    codes = {v.code for v in engine.validate_delegation(ROOT, spec(**kw), NOW)}
    assert code in codes


def test_expired_parent_and_depth_zero_parent_cannot_delegate():
    expired = cap("p", ["billing.refund"], ["customer:C1/*"], "500", "500", depth=1, expires_at=NOW - timedelta(seconds=1))
    assert "PARENT_CAPABILITY_EXPIRED" in {v.code for v in engine.validate_delegation(expired, spec(), NOW)}
    leaf = cap("p", ["billing.refund"], ["customer:C1/*"], "500", "500", depth=0)
    assert "DELEGATION_DEPTH_EXCEEDED" in {v.code for v in engine.validate_delegation(leaf, spec(), NOW)}


def test_child_cannot_outlive_parent():
    parent = cap("p", ["billing.refund"], ["customer:C1/*"], "500", "500", depth=1, expires_at=NOW + timedelta(hours=1))
    codes = {v.code for v in engine.validate_delegation(parent, spec(expires_at=NOW + timedelta(hours=2)), NOW)}
    assert "EXPIRY_EXTENDED" in codes


def test_chain_revalidation_detects_escalation_stored_below_parent():
    child = cap("billing_agent", ["billing.refund"], ["customer:C1/refund_balance"], "900", "900", parent=ROOT)
    violations = engine.check_chain(child, {ROOT.id: ROOT, child.id: child}, NOW)
    assert violations and violations[0].code == "ESCALATION_IN_CHAIN"


def test_effect_checks():
    c = cap("billing_agent", ["billing.refund"], ["customer:C1/refund_balance"], "200", "200")
    ok = effect("k", amount="150", claims=[("customer:C1/refund_balance", "EXCLUSIVE")])
    assert engine.check_effect(c, ok, NOW) == []
    too_much = effect("k", amount="250", claims=[("customer:C1/refund_balance", "EXCLUSIVE")])
    assert {v.code for v in engine.check_effect(c, too_much, NOW)} == {"AMOUNT_EXCEEDS_CAPABILITY"}
    wrong_res = effect("k", amount="10", claims=[("customer:C2/refund_balance", "EXCLUSIVE")])
    assert "RESOURCE_NOT_AUTHORIZED" in {v.code for v in engine.check_effect(c, wrong_res, NOW)}
    wrong_type = effect("k", "crm.update", claims=[("customer:C1/refund_balance", "WRITE")])
    assert "EFFECT_TYPE_NOT_AUTHORIZED" in {v.code for v in engine.check_effect(c, wrong_type, NOW)}
    impostor = effect("k", amount="1", actor="crm_agent", claims=[("customer:C1/refund_balance", "WRITE")])
    assert "ACTOR_NOT_CAPABILITY_SUBJECT" in {v.code for v in engine.check_effect(c, impostor, NOW)}


def test_cumulative_exposure_spans_children():
    t = Tree({"amount": "10000", "cumulative": "10000"})
    for actor, amt in (("a", "1800"), ("b", "3800"), ("c", "5500")):
        tx = t.child(actor, ["billing.refund"], ["customer:C1/*"], "6000")
        t.add(tx, f"k-{actor}", "billing.refund", amount=amt)
    results = {r.subject_id: r for r in engine.cumulative_exposure(t.snapshot())}
    assert results["root_agent"].exposure == Decimal("11100")
    assert not results["root_agent"].passed
    assert all(results[a].passed for a in "abc")  # each child individually valid


def test_aborted_and_failed_effects_do_not_count_against_budget():
    t = Tree({"amount": "1000", "cumulative": "1000"})
    tx = t.child("a", ["billing.refund"], ["customer:C1/*"], "1000")
    t.add(tx, "k1", "billing.refund", amount="900")
    t.add(tx, "k2", "billing.refund", amount="900", state=EffectState.ABORTED)
    t.add(tx, "k3", "billing.refund", amount="900", state=EffectState.FAILED)
    assert {r.subject_id: r.exposure for r in engine.cumulative_exposure(t.snapshot())}["root_agent"] == Decimal("900")


def test_root_grant_requires_trusted_issuer_and_bounded_amounts():
    known = {"billing.refund"}
    bad_issuer = RootCapabilityGrant(issuer="agent:self", allowed_effect_types=["billing.refund"],
                                     allowed_resources=["*"], amount_limit="1", cumulative_amount_limit="1")
    assert engine.validate_root_grant(bad_issuer, known, NOW)[0].code == "UNKNOWN_ISSUER"
    too_big = RootCapabilityGrant(issuer="operator:demo", allowed_effect_types=["billing.refund"],
                                  allowed_resources=["*"], amount_limit="999999", cumulative_amount_limit="1")
    assert "ISSUER_AMOUNT_EXCEEDED" in {v.code for v in engine.validate_root_grant(too_big, known, NOW)}
    unknown_type = RootCapabilityGrant(issuer="operator:demo", allowed_effect_types=["wire.transfer"],
                                       allowed_resources=["*"], amount_limit="1", cumulative_amount_limit="1")
    assert "UNKNOWN_EFFECT_TYPE" in {v.code for v in engine.validate_root_grant(unknown_type, known, NOW)}
