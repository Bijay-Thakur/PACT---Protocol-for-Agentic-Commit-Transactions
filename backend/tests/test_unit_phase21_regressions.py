"""Focused, database-free Phase 2.1 contract regressions."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.adapters.billing_mock import CONTRACT as REFUND
from app.adapters.registry import default_registry
from app.core.evidence import retry_eligibility
from app.policy.workflows import CustomerOffboarding, CompileInput, DraftCap, DraftEffect


def _attempt(number, when, *, status="RESPONSE_LOST", key="stable"):
    return SimpleNamespace(attempt_no=number, started_at=when, status=status,
                           request={"idempotency_key": key, "payload": {"amount": "143.27"},
                                    "contract_version": REFUND.contract_version}, response={})


def test_retry_anchor_includes_abandoned_first_intent_and_expiry():
    now = datetime.now(UTC)
    old = _attempt(1, now - timedelta(seconds=REFUND.idempotency_window_s + 10), status="ABANDONED_BY_RESTART")
    new = _attempt(2, now - timedelta(seconds=1))
    decision = retry_eligibility(REFUND, [old, new], negative_authoritative=False, now=now,
                                 provider_key="stable", payload={"amount": "143.27"},
                                 contract_version=REFUND.contract_version)
    assert decision.allowed is False and decision.kind == "RECONCILE"
    assert retry_eligibility(REFUND, [old, new], negative_authoritative=True,
                             last_observed_attempt_no=1, now=now).allowed is False
    assert retry_eligibility(REFUND, [old, new], negative_authoritative=True,
                             last_observed_attempt_no=2, now=now).kind == "AUTHORITATIVE_NEGATIVE_EVIDENCE"


def test_retry_uses_current_identity_and_margin():
    now = datetime.now(UTC)
    first = _attempt(1, now - timedelta(seconds=REFUND.idempotency_window_s - 1))
    assert not retry_eligibility(REFUND, [first], negative_authoritative=False, now=now).allowed
    first.started_at = now - timedelta(seconds=10)
    assert retry_eligibility(REFUND, [first], negative_authoritative=False, now=now,
                             provider_key="stable", payload={"amount": "143.27"},
                             contract_version=REFUND.contract_version).allowed
    assert not retry_eligibility(REFUND, [first], negative_authoritative=False, now=now,
                                 provider_key="different").allowed


def _offboarding(amount: str | None, eligible: str | None, *, currency: str = "USD"):
    now = datetime.now(UTC)
    root = uuid4()
    fields = [
        ("cancel_subscription", "subscription.cancel", {"customer_id": "C-123"}),
        ("revoke_premium", "identity.revoke", {"customer_id": "C-123", "entitlement": "premium"}),
        ("mark_churned", "crm.update", {"customer_id": "C-123", "lifecycle_state": "churned"}),
        ("confirm_customer", "notification.send", {"customer_id": "C-123", "template": "cancellation_confirmation"}),
    ]
    if amount is not None:
        fields.append(("refund_unused", "billing.refund", {"customer_id": "C-123",
                                                         "amount": amount, "currency": currency}))
    effects = [DraftEffect(uuid4(), root, "requester", kind, slot, payload, [], None,
                           {"observations": {"unused_balance": eligible, "period_start": "2026-10-01"}}
                           if slot == "cancel_subscription" and eligible is not None else {}, True)
               for slot, kind, payload in fields]
    cap = DraftCap(root, "requester", [e.effect_type for e in effects],
                   ["*/customer:C-123/*"], Decimal("500"), Decimal("500"), 0,
                   now + timedelta(hours=1), None)
    return CustomerOffboarding().compile(CompileInput("test", root, "requester",
        {"customer_id": "C-123", "reason": "requested"}, {"approval_threshold": "100"},
        effects, [cap], [], 1, now), default_registry())


def test_offboarding_requires_full_trusted_refund_and_digest_binds_outcome():
    partial = _offboarding("1.00", "143.27")
    assert partial.status == "REJECTED"
    assert "REFUND_AMOUNT_MISMATCH" in {issue.code for issue in partial.issues}
    full = _offboarding("143.27", "143.27")
    assert full.status == "COMPILED", [issue.as_dict() for issue in full.issues]
    assert full.plan["outcomes"][0]["amount"] == "143.27"
    assert full.plan["outcomes"][0]["effect_id"]
    zero = _offboarding(None, "0.00")
    assert zero.status == "COMPILED", [issue.as_dict() for issue in zero.issues]
    assert zero.plan["outcomes"][0]["omission_permitted"]


def test_offboarding_unavailable_fact_currency_and_insufficient_authority_block():
    unavailable = _offboarding("143.27", None)
    assert unavailable.status != "COMPILED"
    assert "FACT_UNAVAILABLE" in {issue.code for issue in unavailable.issues}
    wrong_currency = _offboarding("143.27", "143.27", currency="GBP")
    assert wrong_currency.status != "COMPILED"
    assert any(issue.code == "EFFECT_PAYLOAD_INVALID"
               for issue in wrong_currency.issues)
