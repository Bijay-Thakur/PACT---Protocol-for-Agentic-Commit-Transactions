from app.agents.model_provider import PlanProposal
from app.domain.semantics import (
    assess_compiled_candidate,
    compare_intent_to_proposal,
    extract_intent_semantics,
)


def proposal() -> PlanProposal:
    return PlanProposal(
        objective="Cancel C-EV16",
        requested_workflow="customer_offboarding",
        entity_references={"customer_id": "C-EV16"},
        candidate_actions=[
            "cancel_subscription", "revoke_premium", "mark_churned",
            "refund_unused", "confirm_customer",
        ],
    )


def test_material_clauses_are_grounded_and_not_silently_defaulted():
    cases = {
        "Cancel C-EV16 after the billing period": "TIMING_CONSTRAINT_OMITTED",
        "Cancel C-EV16 and refund in EUR": "CURRENCY_OMITTED_OR_CHANGED",
        "Cancel C-EV16 but also keep it active": "CONTRADICTORY_OUTCOME",
    }
    for source, expected in cases.items():
        issues = compare_intent_to_proposal(extract_intent_semantics(source), proposal())
        assert expected in {issue.code for issue in issues}


def test_compiled_graph_assessment_is_candidate_bound_and_advisory():
    accepted = extract_intent_semantics("Cancel C-EV16 and refund in EUR")
    plan = {
        "params": {"customer_id": "C-EV16"},
        "effects": [
            {"slot": "cancel_subscription", "currency": None},
            {"slot": "refund_unused", "currency": "USD"},
        ],
    }
    assessment = assess_compiled_candidate(accepted, plan, "a" * 64)
    assert assessment.candidate_digest == "a" * 64
    assert assessment.aggregate == "REVIEW_REQUIRED"
    assert "COMPILED_CURRENCY_MISMATCH" in {issue.code for issue in assessment.issues}
    assert not hasattr(assessment, "approved")
