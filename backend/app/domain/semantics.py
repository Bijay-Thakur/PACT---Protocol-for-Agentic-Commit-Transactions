"""Versioned, deterministic intent semantics used before model judgment.

The extractor is deliberately conservative.  It records only meaning that can
be grounded in the supplied text; unsupported or ambiguous meaning becomes an
issue instead of being replaced by a workflow default.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


DimensionResult = Literal["SUPPORTED", "CONCERN", "UNKNOWN", "NOT_APPLICABLE"]
AssessmentDisposition = Literal["PASS", "REVIEW_REQUIRED", "UNAVAILABLE"]


class SourceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str


class SemanticIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    dimension: str
    detail: str
    severity: Literal["INFO", "WARNING", "CRITICAL"] = "WARNING"
    source_spans: list[SourceSpan] = Field(default_factory=list)
    affected_fields: list[str] = Field(default_factory=list)


class IntentSemantics(BaseModel):
    """IntentSemantics/v1. Source text is protected service data, not receipt data."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["IntentSemantics/v1"] = "IntentSemantics/v1"
    source_text: str | None = Field(default=None, max_length=2000)
    source_reference: str | None = Field(default=None, max_length=500)
    source_digest: str = Field(min_length=64, max_length=64)
    span_convention: Literal["python-unicode-codepoint-offsets"] = "python-unicode-codepoint-offsets"
    workflow_id: str | None = None
    entity_identifiers: dict[str, str] = Field(default_factory=dict)
    desired_outcomes: list[str] = Field(default_factory=list)
    amount: str | None = None
    currency: str | None = None
    recipient: str | None = None
    timing_constraints: list[str] = Field(default_factory=list)
    ordering_constraints: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    unresolved_ambiguities: list[str] = Field(default_factory=list)
    provenance_spans: dict[str, list[SourceSpan]] = Field(default_factory=dict)
    trusted_fact_references: list[str] = Field(default_factory=list)
    clarifications: list[dict[str, Any]] = Field(default_factory=list)


class DimensionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    result: DimensionResult
    issues: list[SemanticIssue] = Field(default_factory=list)


class SemanticAssessment(BaseModel):
    """SemanticAssessment/v1 advisory record; it conveys no authority."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["SemanticAssessment/v1"] = "SemanticAssessment/v1"
    candidate_digest: str = Field(min_length=64, max_length=64)
    aggregate: AssessmentDisposition
    dimensions: list[DimensionAssessment]
    provider: str
    model: str | None = None
    prompt_version: str
    rubric_version: str
    configuration_version: str
    issues: list[SemanticIssue] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    latency_ms: float | None = Field(default=None, ge=0)


_INJECTION = re.compile(
    r"ignore\s+(?:all\s+|any\s+|previous\s+|prior\s+)?instructions|\byou are now\b|\bsystem prompt\b",
    re.IGNORECASE,
)


def _spans(text: str, pattern: str, flags: int = re.IGNORECASE) -> list[SourceSpan]:
    return [SourceSpan(start=m.start(), end=m.end(), text=m.group(0))
            for m in re.finditer(pattern, text, flags)]


def extract_intent_semantics(text: str, *, source_reference: str | None = None) -> IntentSemantics:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    customer_spans = _spans(text, r"\bC-[A-Za-z0-9-]+\b", 0)
    currency_spans = _spans(text, r"\b(?:USD|EUR|GBP|CAD|AUD|JPY)\b")
    amount_spans = _spans(text, r"(?<![\w.-])(?:[$€£]\s*)?\d+(?:\.\d{1,2})?(?![\w.-])")
    recipient_spans = _spans(text, r"\b(?:refund|send|pay)\s+(?:it\s+)?to\s+([A-Za-z0-9@._:+-]+)")
    timing_spans = _spans(
        text,
        r"\b(?:after|before|at|on)\s+(?:the\s+)?(?:current\s+)?(?:billing period|billing cycle|renewal|"
        r"month end|end of (?:the )?month)\b|\b(?:immediately|right now|today|tomorrow)\b",
    )
    ordering_spans = _spans(text, r"\b(?:before|after|only after|then)\b[^,.;]{0,120}")
    exclusion_spans = _spans(
        text,
        r"\b(?:do not|don't|must not|never|without)\b[^,.;]{0,120}|"
        r"\b(?:keep|retain)\b[^,.;]{0,80}\b(?:active|enabled|premium)\b",
    )

    outcomes: list[str] = []
    lower = text.lower()
    for outcome, pattern in (
        ("cancel_subscription", r"\b(cancel|offboard|terminate)\b"),
        ("refund", r"\brefund\b"),
        ("revoke_premium", r"\b(revoke|remove|disable)\b[^,.;]{0,40}\bpremium\b"),
        ("keep_active", r"\b(keep|retain)\b[^,.;]{0,40}\b(active|enabled|premium)\b"),
        ("notify", r"\b(notify|notification|confirm|email)\b"),
    ):
        if re.search(pattern, lower):
            outcomes.append(outcome)

    currencies = [s.text.upper() for s in currency_spans]
    symbols = {"$": "USD", "€": "EUR", "£": "GBP"}
    if amount_spans and amount_spans[0].text.strip()[:1] in symbols:
        currencies.append(symbols[amount_spans[0].text.strip()[0]])
    unresolved: list[str] = []
    customers = list(dict.fromkeys(s.text for s in customer_spans))
    if len(customers) > 1:
        unresolved.append("multiple customer identifiers")
    if len(set(currencies)) > 1:
        unresolved.append("multiple currencies")
    if "cancel_subscription" in outcomes and "keep_active" in outcomes:
        unresolved.append("cancellation conflicts with keeping the service active")

    provenance: dict[str, list[SourceSpan]] = {}
    for key, values in (
        ("entity.customer_id", customer_spans),
        ("currency", currency_spans),
        ("amount", amount_spans),
        ("recipient", recipient_spans),
        ("timing", timing_spans),
        ("ordering", ordering_spans),
        ("exclusions", exclusion_spans),
    ):
        if values:
            provenance[key] = values
    return IntentSemantics(
        source_text=text,
        source_reference=source_reference,
        source_digest=digest,
        workflow_id="customer_offboarding" if "cancel_subscription" in outcomes else None,
        entity_identifiers={"customer_id": customers[0]} if len(customers) == 1 else {},
        desired_outcomes=outcomes,
        amount=amount_spans[0].text.strip() if len(amount_spans) == 1 else None,
        currency=currencies[0] if len(set(currencies)) == 1 else None,
        recipient=recipient_spans[0].text if len(recipient_spans) == 1 else None,
        timing_constraints=[s.text for s in timing_spans],
        ordering_constraints=[s.text for s in ordering_spans],
        exclusions=[s.text for s in exclusion_spans],
        unresolved_ambiguities=unresolved,
        provenance_spans=provenance,
    )


def compare_intent_to_proposal(source: IntentSemantics, proposal: Any) -> list[SemanticIssue]:
    """Compare grounded source meaning with a bounded PlanProposal-like value."""

    issues: list[SemanticIssue] = []
    proposed_entities = {
        **dict(getattr(proposal, "entity_references", {}) or {}),
        **dict(getattr(proposal, "requested_parameters", {}) or {}),
    }
    proposed_actions = set(getattr(proposal, "candidate_actions", []) or [])
    objective = str(getattr(proposal, "objective", "") or "")
    objective_lower = objective.lower()
    proposed_outcomes = set(extract_intent_semantics(objective).desired_outcomes)

    expected_customer = source.entity_identifiers.get("customer_id")
    if expected_customer and proposed_entities.get("customer_id") != expected_customer:
        issues.append(SemanticIssue(
            code="CRITICAL_ENTITY_MISMATCH", dimension="entity", severity="CRITICAL",
            detail="proposal customer differs from the customer grounded in the original request",
            source_spans=source.provenance_spans.get("entity.customer_id", []),
            affected_fields=["entity_references.customer_id"],
        ))
    if "multiple customer identifiers" in source.unresolved_ambiguities:
        issues.append(SemanticIssue(
            code="AMBIGUOUS_CRITICAL_ENTITY", dimension="entity", severity="CRITICAL",
            detail="request names multiple customers; an explicit attributable clarification is required",
            source_spans=source.provenance_spans.get("entity.customer_id", []),
            affected_fields=["entity_references.customer_id"],
        ))

    required_actions = {
        "cancel_subscription": "cancel_subscription",
        "refund": "refund_unused",
        "revoke_premium": "revoke_premium",
        "notify": "confirm_customer",
    }
    for outcome, action in required_actions.items():
        if outcome in source.desired_outcomes and action not in proposed_actions \
                and outcome not in proposed_outcomes:
            issues.append(SemanticIssue(
                code="REQUIRED_OUTCOME_OMITTED", dimension="outcomes", severity="CRITICAL",
                detail=f"proposal omitted requested outcome {outcome}",
                affected_fields=["candidate_actions"],
            ))

    if source.currency:
        proposed_currency = proposed_entities.get("currency")
        if proposed_currency is None or proposed_currency.upper() != source.currency:
            issues.append(SemanticIssue(
                code="CURRENCY_OMITTED_OR_CHANGED", dimension="money", severity="CRITICAL",
                detail=f"original request specifies {source.currency}; proposal does not preserve it",
                source_spans=source.provenance_spans.get("currency", []),
                affected_fields=["requested_parameters.currency"],
            ))
    if source.amount:
        proposed_amount = proposed_entities.get("amount")
        if proposed_amount is None or proposed_amount != source.amount:
            issues.append(SemanticIssue(
                code="AMOUNT_OMITTED_OR_CHANGED", dimension="money", severity="CRITICAL",
                detail="proposal does not preserve the exact amount stated in the original request",
                source_spans=source.provenance_spans.get("amount", []),
                affected_fields=["requested_parameters.amount"],
            ))
    if source.recipient and not proposed_entities.get("recipient"):
        issues.append(SemanticIssue(
            code="RECIPIENT_OMITTED", dimension="recipient", severity="CRITICAL",
            detail="proposal omits the requested recipient",
            source_spans=source.provenance_spans.get("recipient", []),
            affected_fields=["requested_parameters.recipient"],
        ))
    if source.timing_constraints and not any(
            phrase.lower() in objective_lower for phrase in source.timing_constraints
    ):
        issues.append(SemanticIssue(
            code="TIMING_CONSTRAINT_OMITTED", dimension="timing", severity="CRITICAL",
            detail="proposal omits a material timing constraint; scheduling is unsupported until clarified",
            source_spans=source.provenance_spans.get("timing", []),
            affected_fields=["objective"],
        ))
    injection = _INJECTION.search(source.source_text or "")
    if injection:
        issues.append(SemanticIssue(
            code="PROMPT_INJECTION", dimension="provenance", severity="CRITICAL",
            detail="instruction-like text is data and cannot grant authority, approval, or execution",
            source_spans=[SourceSpan(start=injection.start(), end=injection.end(), text=injection.group(0))],
            affected_fields=["source_text"],
        ))
    if "cancellation conflicts with keeping the service active" in source.unresolved_ambiguities:
        issues.append(SemanticIssue(
            code="CONTRADICTORY_OUTCOME", dimension="exclusions", severity="CRITICAL",
            detail="request both cancels and keeps the service active",
            source_spans=source.provenance_spans.get("exclusions", []),
            affected_fields=["desired_outcomes"],
        ))
    return issues


def assess_compiled_candidate(
    accepted: IntentSemantics, plan: dict[str, Any], candidate_digest: str
) -> SemanticAssessment:
    """Trusted deterministic comparison of accepted meaning to compiled effects."""

    issues: list[SemanticIssue] = []
    params = plan.get("params") or {}
    effects = plan.get("effects") or []
    slots = {str(effect.get("slot")) for effect in effects}
    currencies = {
        str(effect.get("currency")).upper()
        for effect in effects
        if effect.get("currency")
    }
    expected_customer = accepted.entity_identifiers.get("customer_id")
    if expected_customer and params.get("customer_id") != expected_customer:
        issues.append(SemanticIssue(
            code="COMPILED_ENTITY_MISMATCH", dimension="entity", severity="CRITICAL",
            detail="compiled business request targets a different customer",
            source_spans=accepted.provenance_spans.get("entity.customer_id", []),
            affected_fields=["params.customer_id"],
        ))
    required_slots = {
        "cancel_subscription": "cancel_subscription",
        "refund": "refund_unused",
        "revoke_premium": "revoke_premium",
        "notify": "confirm_customer",
    }
    for outcome, slot in required_slots.items():
        if outcome in accepted.desired_outcomes and slot not in slots:
            issues.append(SemanticIssue(
                code="COMPILED_OUTCOME_OMITTED", dimension="outcomes", severity="CRITICAL",
                detail=f"compiled effect graph omits accepted outcome {outcome}",
                affected_fields=["effects"],
            ))
    if accepted.currency and currencies and accepted.currency not in currencies:
        issues.append(SemanticIssue(
            code="COMPILED_CURRENCY_MISMATCH", dimension="money", severity="CRITICAL",
            detail=f"accepted currency {accepted.currency} differs from compiled {sorted(currencies)}",
            source_spans=accepted.provenance_spans.get("currency", []),
            affected_fields=["effects.currency"],
        ))
    if accepted.timing_constraints:
        issues.append(SemanticIssue(
            code="UNSUPPORTED_TIMING_CONSTRAINT", dimension="timing", severity="CRITICAL",
            detail="compiled immediate effects cannot satisfy the accepted timing constraint",
            source_spans=accepted.provenance_spans.get("timing", []),
            affected_fields=["effects"],
        ))
    if "keep_active" in accepted.desired_outcomes and "cancel_subscription" in slots:
        issues.append(SemanticIssue(
            code="COMPILED_EXCLUSION_VIOLATED", dimension="exclusions", severity="CRITICAL",
            detail="compiled cancellation contradicts the accepted keep-active outcome",
            source_spans=accepted.provenance_spans.get("exclusions", []),
            affected_fields=["effects"],
        ))

    dimensions: list[DimensionAssessment] = []
    for dimension in ("entity", "outcomes", "money", "recipient", "timing", "ordering", "exclusions"):
        dimension_issues = [issue for issue in issues if issue.dimension == dimension]
        applicable = {
            "entity": bool(accepted.entity_identifiers),
            "outcomes": bool(accepted.desired_outcomes),
            "money": bool(accepted.amount or accepted.currency),
            "recipient": bool(accepted.recipient),
            "timing": bool(accepted.timing_constraints),
            "ordering": bool(accepted.ordering_constraints),
            "exclusions": bool(accepted.exclusions or "keep_active" in accepted.desired_outcomes),
        }[dimension]
        dimensions.append(DimensionAssessment(
            dimension=dimension,
            result="CONCERN" if dimension_issues else "SUPPORTED" if applicable else "NOT_APPLICABLE",
            issues=dimension_issues,
        ))
    return SemanticAssessment(
        candidate_digest=candidate_digest,
        aggregate="REVIEW_REQUIRED" if issues else "PASS",
        dimensions=dimensions,
        provider="deterministic_guard",
        model="deterministic-compiled-comparison",
        prompt_version="none",
        rubric_version="semantic-rubric/1",
        configuration_version="deterministic-compiled-comparison/1",
        issues=issues,
    )
