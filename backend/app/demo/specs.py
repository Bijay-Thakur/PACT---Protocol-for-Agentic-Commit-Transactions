"""Deterministic specifications for the golden demo domain.

"Cancel customer C-48291, refund the unused subscription period up to the
authorized amount, revoke premium access, update the CRM record, and send
confirmation only after the required business effects are verified."
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

BUSINESS_DATE = "2026-10-02"
ISSUER = "operator:demo"
ALL_TYPES = ["billing.refund", "crm.update", "identity.revoke", "notification.send", "subscription.cancel"]


def op_keys(cid: str) -> dict[str, str]:
    base = f"customer:{cid}"
    return {
        "cancel": f"{base}/subscription:cancel:{BUSINESS_DATE}",
        "revoke": f"{base}/entitlement:premium:revoke:{BUSINESS_DATE}",
        "crm": f"{base}/crm:lifecycle:churned:{BUSINESS_DATE}",
        "refund": f"{base}/refund:unused-period:{BUSINESS_DATE}",
        "notify": f"{base}/notification:cancellation-confirmation:{BUSINESS_DATE}",
    }


def cancellation_objective(cid: str) -> str:
    return (f"Cancel customer {cid}, refund the unused subscription period up to the authorized amount, "
            "revoke premium access, update the CRM record, and send confirmation only after the required "
            "business effects are verified.")


def cancellation_invariants() -> list[dict[str, Any]]:
    required = ["billing.refund", "subscription.cancel", "identity.revoke", "crm.update"]
    return [
        {"key": "refund_within_authorized_amount", "name": "Refund <= authorized amount (min of delegated cap, unused balance)",
         "phase": "PRE_COMMIT", "expression_type": "effect_amount_lte", "failure_action": "BLOCK_COMMIT",
         "config": {"effect_type": "billing.refund",
                    "limit_refs": ["capability.amount_limit", "observation:subscription.cancel.unused_balance"]}},
        {"key": "root_budget", "name": "Sum of child spend <= root authority",
         "phase": "PRE_COMMIT", "expression_type": "sum_amount_lte", "failure_action": "BLOCK_COMMIT",
         "config": {"limit_ref": "root.capability.cumulative_amount_limit"}},
        {"key": "single_customer", "name": "All effects target the transaction customer",
         "phase": "PRE_COMMIT", "expression_type": "field_matches", "failure_action": "BLOCK_COMMIT",
         "config": {"field": "customer_id", "equals_ref": "metadata.customer_id"}},
        {"key": "unique_operations", "name": "Each logical operation appears once",
         "phase": "PRE_COMMIT", "expression_type": "unique_operation_keys", "failure_action": "BLOCK_COMMIT"},
        {"key": "notification_gated", "name": "Confirmation depends on all required business effects",
         "phase": "PRE_COMMIT", "expression_type": "depends_on_verified", "failure_action": "BLOCK_COMMIT",
         "config": {"effect_type": "notification.send", "requires": required}},
        {"key": "notification_after_verification", "name": "Confirmation dispatched only after prerequisites verified",
         "phase": "POST_EXECUTION", "expression_type": "depends_on_verified", "failure_action": "HUMAN_REQUIRED",
         "config": {"effect_type": "notification.send", "requires": required}},
        {"key": "cancel_requires_revoke", "name": "Cancelled subscription requires entitlement revoked",
         "phase": "POST_EXECUTION", "expression_type": "implies_verified", "failure_action": "COMPENSATE",
         "config": {"if_effect_type": "subscription.cancel", "then_effect_type": "identity.revoke"}},
        {"key": "one_refund_per_operation", "name": "Exactly one external refund per operation_key",
         "phase": "FINAL", "expression_type": "single_external_match", "failure_action": "HUMAN_REQUIRED",
         "config": {"effect_type": "billing.refund"}},
    ]


def root_spec(cid: str, *, amount_limit: str = "500.00", auto_reconcile: bool = True, scenario: str = "success") -> dict[str, Any]:
    return {
        "objective": cancellation_objective(cid),
        "actor_id": "root_agent",
        "capability": {
            "issuer": ISSUER,
            "allowed_effect_types": ALL_TYPES,
            "allowed_resources": [f"customer:{cid}/*"],
            "amount_limit": amount_limit,
            "cumulative_amount_limit": amount_limit,
            "delegation_depth": 2,
        },
        "participants": ["billing_agent", "subscription_agent", "identity_agent", "crm_agent", "notification_agent"],
        "invariants": cancellation_invariants(),
        "required_effect_types": ALL_TYPES,
        "recovery_policy": {"on_effect_failure": "COMPENSATE", "auto_reconcile": auto_reconcile},
        "metadata": {"customer_id": cid, "scenario": scenario, "business_date": BUSINESS_DATE},
    }


def _cap(types: list[str], resources: list[str], amount: str = "0.00") -> dict[str, Any]:
    return {"allowed_effect_types": types, "allowed_resources": resources,
            "amount_limit": amount, "cumulative_amount_limit": amount, "delegation_depth": 0}


def child_specs(cid: str, *, refund_amount: str = "143.27", billing_cap: str = "200.00") -> list[dict[str, Any]]:
    """One child transaction per agent, each with a narrowly delegated capability and its proposal."""
    k = op_keys(cid)
    c = f"customer:{cid}"
    return [
        {"actor_id": "subscription_agent", "objective": "Cancel the subscription",
         "capability": _cap(["subscription.cancel"], [f"{c}/subscription"]),
         "effects": [{"effect_type": "subscription.cancel", "actor_id": "subscription_agent", "operation_key": k["cancel"],
                      "payload": {"customer_id": cid, "reason": "customer requested cancellation"}}]},
        {"actor_id": "identity_agent", "objective": "Revoke premium entitlement",
         "capability": _cap(["identity.revoke"], [f"{c}/entitlement"]),
         "effects": [{"effect_type": "identity.revoke", "actor_id": "identity_agent", "operation_key": k["revoke"],
                      "payload": {"customer_id": cid, "entitlement": "premium"}, "depends_on": [k["cancel"]]}]},
        {"actor_id": "crm_agent", "objective": "Mark the account churned in CRM",
         "capability": _cap(["crm.update"], [f"{c}/crm_record"]),
         "effects": [{"effect_type": "crm.update", "actor_id": "crm_agent", "operation_key": k["crm"],
                      "payload": {"customer_id": cid, "lifecycle_state": "churned",
                                  "note": "Cancelled via PACT transaction"},
                      "depends_on": [k["cancel"]]}]},
        {"actor_id": "billing_agent", "objective": "Refund the unused subscription period",
         "capability": _cap(["billing.refund"], [f"{c}/refund_balance"], billing_cap),
         "effects": [{"effect_type": "billing.refund", "actor_id": "billing_agent", "operation_key": k["refund"],
                      "payload": {"customer_id": cid, "amount": refund_amount, "reason": "unused-period refund"},
                      "resource_claims": [{"resource": f"{c}/refund_balance", "mode": "EXCLUSIVE"}],
                      "depends_on": [k["cancel"], k["revoke"]]}]},
        {"actor_id": "notification_agent", "objective": "Send cancellation confirmation",
         "capability": _cap(["notification.send"], [f"{c}/notifications"]),
         "effects": [{"effect_type": "notification.send", "actor_id": "notification_agent", "operation_key": k["notify"],
                      "payload": {"customer_id": cid, "template": "cancellation_confirmation",
                                  "variables": {"refund_amount": refund_amount}},
                      "depends_on": [k["refund"], k["cancel"], k["revoke"], k["crm"]]}]},
    ]


BUDGET_LINES = [("platform", "1800.00"), ("support", "3800.00"), ("infra", "5500.00")]


def budget_root_spec(cid: str, budget: str = "10000.00") -> dict[str, Any]:
    return {
        "objective": f"Refund enterprise customer {cid} for a multi-product outage across three product lines "
                     f"within a shared ${budget} remediation budget.",
        "actor_id": "root_agent",
        "capability": {"issuer": ISSUER, "allowed_effect_types": ["billing.refund"],
                       "allowed_resources": [f"customer:{cid}/*"], "amount_limit": budget,
                       "cumulative_amount_limit": budget, "delegation_depth": 1},
        "participants": [f"billing_agent_{line}" for line, _ in BUDGET_LINES],
        "invariants": [
            {"key": "root_budget", "name": "Combined refunds <= shared remediation budget",
             "phase": "PRE_COMMIT", "expression_type": "sum_amount_lte", "failure_action": "BLOCK_COMMIT",
             "config": {"effect_type": "billing.refund", "limit_ref": "root.capability.cumulative_amount_limit"}},
            {"key": "unique_operations", "name": "Each logical operation appears once",
             "phase": "PRE_COMMIT", "expression_type": "unique_operation_keys", "failure_action": "BLOCK_COMMIT"},
        ],
        "required_effect_types": ["billing.refund"],
        "metadata": {"customer_id": cid, "scenario": "budget-conflict", "business_date": BUSINESS_DATE},
    }


def budget_child_specs(cid: str, local_cap: str = "6000.00") -> list[dict[str, Any]]:
    out = []
    for line, amount in BUDGET_LINES:
        charge = f"ch_{cid}_{line}"
        actor = f"billing_agent_{line}"
        out.append({
            "actor_id": actor, "objective": f"Refund the {line} outage credit",
            "capability": _cap(["billing.refund"], [f"customer:{cid}/refund_balance/{charge}"], local_cap),
            "effects": [{"effect_type": "billing.refund", "actor_id": actor,
                         "operation_key": f"customer:{cid}/refund:outage-{line}:{BUSINESS_DATE}",
                         "payload": {"customer_id": cid, "amount": amount, "charge_id": charge,
                                     "reason": f"{line} outage credit"}}],
        })
    return out


def full_cancellation_spec(cid: str, refund_amount: str = "143.27", amount_limit: str = "500.00") -> dict[str, Any]:
    """Whole TransactionSpec (root + children) as a planner would propose it."""
    cap = str(min(Decimal(amount_limit), Decimal("200.00")))
    return {**root_spec(cid, amount_limit=amount_limit, scenario="planner"),
            "children": child_specs(cid, refund_amount=refund_amount, billing_cap=cap)}
