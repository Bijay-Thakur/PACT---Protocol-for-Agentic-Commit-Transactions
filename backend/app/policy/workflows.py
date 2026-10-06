"""Trusted workflow definitions (policy packs) and the plan compiler.

The server - not the agent or model - chooses the workflow, its mandatory
effects, dependencies, invariants, refund eligibility, approval requirements and
budget scopes. Agents may propose effects into a draft; the compiler maps them
onto the workflow's action slots and returns either a reviewable compiled plan,
``NEEDS_CLARIFICATION``, or a typed rejection.

Compilation only uses registered typed predicates (the invariant engine's
expression types). No model-generated code, SQL or expressions are executed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.adapters.registry import EffectRegistry
from app.domain.errors import ValidationFailed
from app.domain.invariant import InvariantDefinition
from app.policy.digest import digest, fingerprint

MONEY = Decimal("0.01")
SUPPORTED_CURRENCIES = {"USD"}


# --------------------------------------------------------------------------- inputs


@dataclass
class DraftEffect:
    id: UUID
    transaction_id: UUID
    actor: str
    effect_type: str
    slot: str | None
    payload: dict[str, Any]
    declared_depends_on: list[str]
    client_operation_key: str | None
    prepare_evidence: dict[str, Any]
    prepared: bool
    prior_identity: str | None = None


@dataclass
class DraftCap:
    transaction_id: UUID
    subject: str
    allowed_effect_types: list[str]
    allowed_resources: list[str]
    amount_limit: Decimal | None
    cumulative_limit: Decimal | None
    delegation_depth: int
    expires_at: datetime | None
    parent_capability_id: UUID | None


@dataclass
class CompileInput:
    tenant_id: str
    root_id: UUID
    root_actor: str
    params: dict[str, Any]
    grant: dict[str, Any]
    effects: list[DraftEffect]
    caps: list[DraftCap]
    unprepared_children: list[str]
    epoch: int
    now: datetime


@dataclass
class Issue:
    code: str
    detail: str
    slot: str | None = None
    effect_id: str | None = None
    kind: str = "REJECT"  # REJECT | CLARIFY

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "slot": self.slot, "effect_id": self.effect_id,
                "kind": self.kind}


@dataclass
class CompiledEffect:
    effect_id: UUID
    transaction_id: UUID
    slot: str
    effect_type: str
    actor: str
    payload: dict[str, Any]
    amount: Decimal | None
    currency: str | None
    max_exposure: Decimal | None
    claims: list[dict[str, str]]
    depends_on_slots: list[str]
    depends_on: list[UUID] = field(default_factory=list)
    identity: str = ""
    fingerprint: str = ""
    provider: str = ""
    contract: dict[str, str] = field(default_factory=dict)
    preconditions: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompileResult:
    status: str  # COMPILED | REJECTED | NEEDS_CLARIFICATION
    issues: list[Issue]
    effects: list[CompiledEffect] = field(default_factory=list)
    invariants: list[InvariantDefinition] = field(default_factory=list)
    budgets: list[dict[str, Any]] = field(default_factory=list)
    approval: dict[str, Any] = field(default_factory=lambda: {"required": False})
    business_request_key: str = ""
    skipped: list[dict[str, str]] = field(default_factory=list)
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    plan: dict[str, Any] = field(default_factory=dict)
    digest: str | None = None

    def summary(self) -> dict[str, Any]:
        return {"status": self.status, "issues": [i.as_dict() for i in self.issues], "digest": self.digest,
                "approval": self.approval, "business_request_key": self.business_request_key,
                "skipped": self.skipped, "outcomes": self.outcomes,
                "budgets": [{**b, "limit": str(b["limit"])} for b in self.budgets]}


# --------------------------------------------------------------------------- helpers


def money(v: Any, field_name: str = "amount") -> Decimal:
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise ValidationFailed(f"{field_name} is not a decimal amount", code="INVALID_AMOUNT") from None
    if not d.is_finite():
        raise ValidationFailed(f"{field_name} must be finite", code="INVALID_AMOUNT")
    if d != d.quantize(MONEY):
        raise ValidationFailed(f"{field_name} has more than 2 decimal places", code="INVALID_PRECISION")
    return d.quantize(MONEY)


@dataclass(frozen=True)
class SlotSpec:
    name: str
    effect_type: str
    required: bool = True
    min_count: int = 1
    max_count: int = 1
    depends_on: tuple[str, ...] = ()
    fixed: dict[str, Any] = field(default_factory=dict)


class Workflow:
    key: str
    version: str
    policy_version: str
    title: str
    description: str
    params_model: type[BaseModel]
    slots: tuple[SlotSpec, ...]

    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.params_model(**params).model_dump(mode="json")
        except ValidationError as exc:
            raise ValidationFailed(f"invalid business request for {self.key}", code="INVALID_BUSINESS_REQUEST",
                                   details=exc.errors(include_url=False, include_context=False)) from None

    def root_capability(self, params: dict[str, Any], grant: dict[str, Any], now: datetime) -> dict[str, Any]:
        raise NotImplementedError

    def compile(self, inp: CompileInput, registry: EffectRegistry) -> CompileResult:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {"key": self.key, "version": self.version, "policy_version": self.policy_version,
                "title": self.title, "description": self.description,
                "params_schema": self.params_model.model_json_schema(),
                "slots": [{"name": s.name, "effect_type": s.effect_type, "required": s.required,
                           "min": s.min_count, "max": s.max_count, "depends_on": list(s.depends_on)}
                          for s in self.slots]}

    # ---- shared compilation machinery ------------------------------------
    def _bind_slots(self, inp: CompileInput, issues: list[Issue]) -> dict[str, list[DraftEffect]]:
        by_type: dict[str, list[SlotSpec]] = {}
        for sl in self.slots:
            by_type.setdefault(sl.effect_type, []).append(sl)
        bound: dict[str, list[DraftEffect]] = {s.name: [] for s in self.slots}
        for e in sorted(inp.effects, key=lambda e: str(e.id)):
            candidates = by_type.get(e.effect_type, [])
            if not candidates:
                issues.append(Issue("EFFECT_NOT_PERMITTED_IN_WORKFLOW",
                                    f"{e.effect_type} is not an action of workflow {self.key}", effect_id=str(e.id)))
                continue
            if e.slot is not None:
                match = [c for c in candidates if c.name == e.slot]
                if not match:
                    issues.append(Issue("UNKNOWN_SLOT", f"slot {e.slot!r} does not accept {e.effect_type}",
                                        effect_id=str(e.id)))
                    continue
                bound[match[0].name].append(e)
            elif len(candidates) == 1:
                bound[candidates[0].name].append(e)
            else:
                issues.append(Issue("AMBIGUOUS_SLOT", f"{e.effect_type} needs an explicit slot",
                                    effect_id=str(e.id), kind="CLARIFY"))
        for sl in self.slots:
            n = len(bound[sl.name])
            if n > sl.max_count:
                issues.append(Issue("SLOT_CARDINALITY", f"slot {sl.name} accepts at most {sl.max_count}, got {n}",
                                    slot=sl.name))
        return bound

    def _finish(self, inp: CompileInput, registry: EffectRegistry, issues: list[Issue], compiled: list[CompiledEffect],
                invariants: list[InvariantDefinition], budgets: list[dict], approval: dict, brk: str,
                skipped: list[dict]) -> CompileResult:
        if inp.unprepared_children:
            issues.append(Issue("CHILD_NOT_PREPARED",
                                f"children not locally prepared: {', '.join(sorted(inp.unprepared_children))}"))
        # Resolve slot dependencies to concrete effects; agent-declared deps are kept only if resolvable.
        by_slot: dict[str, list[CompiledEffect]] = {}
        for c in compiled:
            by_slot.setdefault(c.slot, []).append(c)
        client_keys = {e.client_operation_key: e.id for e in inp.effects if e.client_operation_key}
        prior_identities = {e.prior_identity: e.id for e in inp.effects if e.prior_identity}
        for c in compiled:
            deps: set[UUID] = set()
            for slot_name in c.depends_on_slots:
                deps |= {d.effect_id for d in by_slot.get(slot_name, [])}
            src = next(e for e in inp.effects if e.id == c.effect_id)
            for d in src.declared_depends_on:
                if d in by_slot:
                    deps |= {x.effect_id for x in by_slot[d]}
                elif d in client_keys:
                    deps.add(client_keys[d])
                elif d in prior_identities:
                    deps.add(prior_identities[d])
                else:
                    issues.append(Issue("DEPENDENCY_UNRESOLVED", f"dependency {d!r} matches no slot or effect",
                                        effect_id=str(c.effect_id)))
            deps.discard(c.effect_id)
            c.depends_on = sorted(deps, key=str)
        if _has_cycle(compiled):
            issues.append(Issue("DEPENDENCY_CYCLE", "declared dependencies form a cycle"))
        # Contract pins, identities, fingerprints.
        for c in compiled:
            contract = registry.contract(c.effect_type)
            c.contract = {"id": contract.contract_id, "version": contract.contract_version, "hash": contract.contract_hash}
            c.provider = contract.provider
            resource = c.claims[0]["resource"] if c.claims else f"{contract.provider}/unscoped"
            c.identity = f"op:{inp.tenant_id}/{contract.provider}/{brk}/{c.slot}/{resource}/e{inp.epoch}"
            c.fingerprint = fingerprint(c.payload)
        status = "COMPILED"
        if any(i.kind == "REJECT" for i in issues):
            status = "REJECTED"
        elif issues:
            status = "NEEDS_CLARIFICATION"
        result = CompileResult(status=status, issues=issues, effects=compiled, invariants=invariants,
                               budgets=budgets, approval=approval, business_request_key=brk, skipped=skipped)
        if status == "COMPILED":
            result.plan = self._plan_document(inp, result)
            result.digest = digest(result.plan)
        return result

    def _plan_document(self, inp: CompileInput, r: CompileResult) -> dict[str, Any]:
        """Everything material to execution. Approval binds the digest of this document."""
        projection = {"version": "Projection/v1",
                      "mode": "ISOLATED_CANDIDATE" if self.key == "code_sandbox_change"
                              else "CONTRACT_PROJECTED",
                      "required_outcomes": r.outcomes,
                      "expected_changes": [{"effect_id": str(c.effect_id), "slot": c.slot,
                                            "effect_type": c.effect_type,
                                            "resource": c.claims[0]["resource"] if c.claims else None,
                                            "amount": str(c.amount) if c.amount is not None else None,
                                            "currency": c.currency,
                                            "preconditions": c.preconditions}
                                           for c in sorted(r.effects, key=lambda c: (c.slot, str(c.effect_id)))],
                      "assumptions": ["Provider preconditions are rechecked before dispatch."],
                      "unsupported_predictions": ["External application is not known until observed."]}
        return {
            "workflow": {"key": self.key, "version": self.version, "policy_version": self.policy_version},
            "tenant_id": inp.tenant_id, "root_id": str(inp.root_id), "root_actor": inp.root_actor,
            "business_request_key": r.business_request_key, "params": inp.params, "epoch": inp.epoch,
            "effects": [{
                "effect_id": str(c.effect_id), "transaction_id": str(c.transaction_id), "slot": c.slot,
                "effect_type": c.effect_type, "actor": c.actor, "contract": c.contract, "payload": c.payload,
                "amount": c.amount, "currency": c.currency, "max_exposure": c.max_exposure, "claims": c.claims,
                "depends_on": [str(d) for d in c.depends_on], "identity": c.identity, "fingerprint": c.fingerprint,
                "preconditions": c.preconditions,
            } for c in sorted(r.effects, key=lambda c: (c.slot, str(c.effect_id)))],
            "authority": [{
                "transaction_id": str(cap.transaction_id), "subject": cap.subject,
                "allowed_effect_types": sorted(cap.allowed_effect_types),
                "allowed_resources": sorted(cap.allowed_resources), "amount_limit": cap.amount_limit,
                "cumulative_limit": cap.cumulative_limit, "delegation_depth": cap.delegation_depth,
                "expires_at": cap.expires_at,
            } for cap in sorted(inp.caps, key=lambda c: (c.subject, str(c.transaction_id)))],
            "invariants": [i.model_dump(mode="json") for i in sorted(r.invariants, key=lambda i: i.key)],
            "budgets": [{**b, "limit": b["limit"]} for b in sorted(r.budgets, key=lambda b: b["key"])],
            "approval": r.approval, "skipped": r.skipped, "outcomes": r.outcomes,
            "projection": projection,
        }


def _has_cycle(compiled: list[CompiledEffect]) -> bool:
    graph = {c.effect_id: set(c.depends_on) for c in compiled}
    state: dict[UUID, int] = {}

    def visit(n: UUID) -> bool:
        state[n] = 1
        for d in graph.get(n, ()):
            if state.get(d) == 1 or (state.get(d) is None and d in graph and visit(d)):
                return True
        state[n] = 2
        return False

    return any(state.get(n) is None and visit(n) for n in graph)


def _customer_resources(cid: str) -> list[str]:
    return [f"{p}/customer:{cid}/*" for p in ("billing", "subscription", "identity", "crm", "notification")]


def _base_effect(e: DraftEffect, slot: SlotSpec, registry: EffectRegistry, issues: list[Issue],
                 fixed: dict[str, Any] | None = None) -> CompiledEffect | None:
    contract = registry.contract(e.effect_type)
    payload = {**e.payload, **(e.prepare_evidence.get("resolved") or {}), **slot.fixed, **(fixed or {})}
    try:
        payload = contract.payload_model(**payload).model_dump(mode="json")
    except ValidationError as exc:
        issues.append(Issue("EFFECT_PAYLOAD_INVALID", f"{slot.name}: {exc.errors(include_url=False)[0]['msg']}",
                            slot=slot.name, effect_id=str(e.id)))
        return None
    amount = currency = None
    if contract.amount_field:
        amount = money(payload[contract.amount_field])
        currency = payload.get("currency", "USD")
        if currency not in SUPPORTED_CURRENCIES or currency not in contract.supported_currencies:
            issues.append(Issue("UNSUPPORTED_CURRENCY", f"{currency} is not supported", slot=slot.name,
                                effect_id=str(e.id)))
    claims = [c.model_dump(mode="json") for c in (contract.required_claims(payload) if contract.required_claims else [])]
    max_exp = e.prepare_evidence.get("max_exposure")
    return CompiledEffect(effect_id=e.id, transaction_id=e.transaction_id, slot=slot.name,
                          effect_type=e.effect_type, actor=e.actor, payload=payload, amount=amount,
                          currency=currency, max_exposure=Decimal(str(max_exp)) if max_exp is not None else amount,
                          claims=claims, depends_on_slots=list(slot.depends_on),
                          preconditions=e.prepare_evidence.get("preconditions") or {})


CUSTOMER_ID = r"^C-[A-Za-z0-9\-]{1,60}$"


# =========================================================================== offboarding


class OffboardingParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(pattern=CUSTOMER_ID)
    reason: str = Field(default="customer requested cancellation", max_length=500)


class CustomerOffboarding(Workflow):
    key = "customer_offboarding"
    version = "1.1.0"
    policy_version = "offboarding-policy/2026-10-05"
    title = "Customer offboarding"
    description = ("Cancel the subscription, revoke premium access, mark the CRM account churned, refund the "
                   "eligible unused period, and confirm to the customer only after the business effects verify.")
    params_model = OffboardingParams
    slots = (
        SlotSpec("cancel_subscription", "subscription.cancel"),
        SlotSpec("revoke_premium", "identity.revoke", depends_on=("cancel_subscription",),
                 fixed={"entitlement": "premium"}),
        SlotSpec("mark_churned", "crm.update", depends_on=("cancel_subscription",),
                 fixed={"lifecycle_state": "churned"}),
        SlotSpec("refund_unused", "billing.refund", required=False,
                 depends_on=("cancel_subscription", "revoke_premium")),
        SlotSpec("confirm_customer", "notification.send",
                 depends_on=("cancel_subscription", "revoke_premium", "mark_churned", "refund_unused")),
    )

    def root_capability(self, params: dict[str, Any], grant: dict[str, Any], now: datetime) -> dict[str, Any]:
        limit = grant.get("amount_limit", "0")
        return {"allowed_effect_types": sorted({s.effect_type for s in self.slots}),
                "allowed_resources": _customer_resources(params["customer_id"]),
                "amount_limit": limit, "cumulative_amount_limit": grant.get("cumulative_amount_limit", limit),
                "delegation_depth": int(grant.get("delegation_depth", 1)),
                "expires_at": now + timedelta(hours=float(grant.get("ttl_hours", 24)))}

    def compile(self, inp: CompileInput, registry: EffectRegistry) -> CompileResult:
        issues: list[Issue] = []
        cid = inp.params["customer_id"]
        bound = self._bind_slots(inp, issues)
        for slot_effects in bound.values():
            for e in slot_effects:
                if e.payload.get("customer_id") != cid:  # A28: effects for another customer satisfy nothing
                    issues.append(Issue("EFFECT_TARGETS_OTHER_CUSTOMER",
                                        f"{e.effect_type} targets {e.payload.get('customer_id')!r}, request is {cid!r}",
                                        effect_id=str(e.id)))
        slot = {s.name: s for s in self.slots}
        compiled: dict[str, CompiledEffect] = {}
        for name in ("cancel_subscription", "revoke_premium", "mark_churned"):
            effects = bound[name]
            if not effects:
                issues.append(Issue("MISSING_REQUIRED_EFFECT", f"required action {name} was not proposed", slot=name))
                continue
            c = _base_effect(effects[0], slot[name], registry, issues)
            if c:
                compiled[name] = c

        # Trusted fact: unused-period value, from the subscription provider via prepare (not the agent).
        cancel_ev = bound["cancel_subscription"][0].prepare_evidence if bound["cancel_subscription"] else {}
        facts = cancel_ev.get("observations") or {}
        eligible = money(facts["unused_balance"], "unused_balance") if "unused_balance" in facts else None
        period = facts.get("period_start")
        if eligible is None and bound["cancel_subscription"]:
            issues.append(Issue("FACT_UNAVAILABLE", "unused balance could not be read from the subscription provider",
                                slot="cancel_subscription", kind="CLARIFY"))
        skipped: list[dict[str, str]] = []
        refunds = bound["refund_unused"]
        refund_amount = Decimal("0.00")
        if eligible is not None:
            if eligible > 0 and not refunds:
                issues.append(Issue("MISSING_REQUIRED_EFFECT",
                                    f"policy requires refunding the eligible unused period ({eligible} USD)",
                                    slot="refund_unused"))
            elif eligible == 0 and not refunds:
                skipped.append({"slot": "refund_unused", "reason": "eligible unused value is 0.00; omission permitted"})
            for e in refunds:
                c = _base_effect(e, slot["refund_unused"], registry, issues)
                if not c:
                    continue
                if c.amount is not None and c.amount != eligible:
                    issues.append(Issue("REFUND_AMOUNT_MISMATCH",
                                        f"offboarding requires the full eligible unused value {eligible} USD; proposed {c.amount}",
                                        slot="refund_unused", effect_id=str(e.id)))
                elif c.amount is not None and c.amount <= 0:
                    issues.append(Issue("INVALID_AMOUNT", "refund must be positive", slot="refund_unused"))
                compiled["refund_unused"] = c
                refund_amount = c.amount or Decimal("0.00")

        # Confirmation content is server-derived: it may not claim a refund that is not part of the plan.
        notes = bound["confirm_customer"]
        if not notes:
            issues.append(Issue("MISSING_REQUIRED_EFFECT", "required action confirm_customer was not proposed",
                                slot="confirm_customer"))
        else:
            with_refund = "refund_unused" in compiled
            fixed = {"template": "cancellation_confirmation" if with_refund else "cancellation_confirmation_no_refund",
                     "variables": {"refund_amount": str(refund_amount) if with_refund else "0.00",
                                   "currency": "USD", "customer_id": cid}}
            c = _base_effect(notes[0], slot["confirm_customer"], registry, issues, fixed=fixed)
            if c:
                compiled["confirm_customer"] = c

        brk = f"offboarding/customer:{cid}/period:{period or 'unknown'}"
        approval_threshold = money(inp.grant.get("approval_threshold", "100.00"))
        approval = {"required": refund_amount > approval_threshold, "role": "refund_approver",
                    "reason": (f"refund {refund_amount} USD exceeds the {approval_threshold} USD approval threshold"
                               if refund_amount > approval_threshold else "below approval threshold")}
        budgets = []
        if eligible is not None:
            budgets.append({"key": f"budget/customer:{cid}/unused-period-refund/{period}", "limit": eligible,
                            "currency": "USD", "slot": "refund_unused",
                            "description": "Eligible unused-period value (subscription provider fact)"})
        if inp.grant.get("daily_refund_limit"):
            budgets.append({"key": f"budget/principal:{inp.root_actor}/refunds/{inp.now.date().isoformat()}",
                            "limit": money(inp.grant["daily_refund_limit"]), "currency": "USD",
                            "slot": "refund_unused", "description": "Daily refund authority of the initiating principal"})
        result = self._finish(inp, registry, issues, list(compiled.values()), offboarding_invariants(), budgets,
                              approval, brk, skipped)
        if eligible is not None:
            result.outcomes = [{"id": "full_eligible_refund", "subject": cid,
                                "predicate": "observed_refund_equals", "amount": str(eligible), "currency": "USD",
                                "evidence": "final_billing_observation",
                                "effect_id": str(compiled["refund_unused"].effect_id) if "refund_unused" in compiled else None,
                                "omission_permitted": eligible == 0}]
            if result.status == "COMPILED":
                result.plan = self._plan_document(inp, result)
                result.digest = digest(result.plan)
        return result


def offboarding_invariants() -> list[InvariantDefinition]:
    required = ["subscription.cancel", "identity.revoke", "crm.update"]
    return [InvariantDefinition(**d) for d in [
        {"key": "refund_within_authorized_amount", "name": "Refund <= min(delegated cap, unused balance)",
         "phase": "PRE_COMMIT", "expression_type": "effect_amount_lte", "failure_action": "BLOCK_COMMIT",
         "config": {"effect_type": "billing.refund",
                    "limit_refs": ["capability.amount_limit", "observation:subscription.cancel.unused_balance"]}},
        {"key": "root_budget", "name": "Sum of child spend <= root authority", "phase": "PRE_COMMIT",
         "expression_type": "sum_amount_lte", "failure_action": "BLOCK_COMMIT",
         "config": {"limit_ref": "root.capability.cumulative_amount_limit"}},
        {"key": "single_customer", "name": "All effects target the transaction customer", "phase": "PRE_COMMIT",
         "expression_type": "field_matches", "failure_action": "BLOCK_COMMIT",
         "config": {"field": "customer_id", "equals_ref": "metadata.customer_id"}},
        {"key": "unique_operations", "name": "Each logical operation appears once", "phase": "PRE_COMMIT",
         "expression_type": "unique_operation_keys", "failure_action": "BLOCK_COMMIT"},
        {"key": "notification_gated", "name": "Confirmation depends on all required business effects",
         "phase": "PRE_COMMIT", "expression_type": "depends_on_verified", "failure_action": "BLOCK_COMMIT",
         "config": {"effect_type": "notification.send", "requires": [*required, "billing.refund"]}},
        {"key": "notification_after_verification", "name": "Confirmation dispatched only after prerequisites verified",
         "phase": "POST_EXECUTION", "expression_type": "depends_on_verified", "failure_action": "HUMAN_REQUIRED",
         "config": {"effect_type": "notification.send", "requires": [*required, "billing.refund"]}},
        {"key": "cancel_requires_revoke", "name": "Cancelled subscription requires entitlement revoked",
         "phase": "POST_EXECUTION", "expression_type": "implies_verified", "failure_action": "COMPENSATE",
         "config": {"if_effect_type": "subscription.cancel", "then_effect_type": "identity.revoke"}},
        {"key": "one_refund_per_operation", "name": "Exactly one external refund per operation",
         "phase": "FINAL", "expression_type": "single_external_match", "failure_action": "HUMAN_REQUIRED",
         "config": {"effect_type": "billing.refund"}},
    ]]


# =========================================================================== remediation


class RemediationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(pattern=CUSTOMER_ID)
    incident_id: str = Field(pattern=r"^INC-[A-Za-z0-9\-]{1,40}$")


class CustomerRemediation(Workflow):
    key = "customer_remediation"
    version = "1.0.0"
    policy_version = "remediation-policy/2026-10-03"
    title = "Outage remediation refunds"
    description = "Refund one or more charges for an incident under a shared remediation budget."
    params_model = RemediationParams
    slots = (SlotSpec("refund_line", "billing.refund", min_count=1, max_count=10),)

    def root_capability(self, params: dict[str, Any], grant: dict[str, Any], now: datetime) -> dict[str, Any]:
        budget = grant.get("remediation_budget", "0")
        return {"allowed_effect_types": ["billing.refund"],
                "allowed_resources": [f"billing/customer:{params['customer_id']}/*"],
                "amount_limit": budget, "cumulative_amount_limit": budget,
                "delegation_depth": int(grant.get("delegation_depth", 1)),
                "expires_at": now + timedelta(hours=float(grant.get("ttl_hours", 24)))}

    def compile(self, inp: CompileInput, registry: EffectRegistry) -> CompileResult:
        issues: list[Issue] = []
        cid, incident = inp.params["customer_id"], inp.params["incident_id"]
        bound = self._bind_slots(inp, issues)
        lines = bound["refund_line"]
        if not lines:
            issues.append(Issue("MISSING_REQUIRED_EFFECT", "at least one refund line is required", slot="refund_line"))
        compiled: list[CompiledEffect] = []
        for e in lines:
            if e.payload.get("customer_id") != cid:
                issues.append(Issue("EFFECT_TARGETS_OTHER_CUSTOMER", f"refund targets {e.payload.get('customer_id')}",
                                    effect_id=str(e.id)))
                continue
            if not e.payload.get("charge_id"):
                issues.append(Issue("CHARGE_REQUIRED", "remediation refunds must name a charge", effect_id=str(e.id),
                                    kind="CLARIFY"))
                continue
            c = _base_effect(e, self.slots[0], registry, issues)
            if c:
                compiled.append(c)
        charges = [c.payload.get("charge_id") for c in compiled]
        if len(charges) != len(set(charges)):
            issues.append(Issue("DUPLICATE_CHARGE", "two refund lines target the same charge"))
        budget = money(inp.grant.get("remediation_budget", "0"))
        total = sum((c.amount or Decimal("0") for c in compiled), Decimal("0.00"))
        if total > budget:  # A04: individually valid lines, jointly over the shared budget
            issues.append(Issue("SHARED_BUDGET_EXCEEDED",
                                f"combined refunds {total} USD exceed the shared remediation budget {budget} USD",
                                slot="refund_line"))
        # Each line gets a distinct slot instance name so identities differ per charge.
        for c in compiled:
            c.slot = "refund_line"
        budgets = [{"key": f"budget/remediation/customer:{cid}/incident:{incident}", "limit": budget,
                    "currency": "USD", "slot": "refund_line", "description": "Shared remediation budget (grant)"}]
        approval = {"required": True, "role": "finance_approver", "reason": "remediation refunds always need approval"}
        invariants = [InvariantDefinition(**d) for d in [
            {"key": "root_budget", "name": "Combined refunds <= shared remediation budget", "phase": "PRE_COMMIT",
             "expression_type": "sum_amount_lte", "failure_action": "BLOCK_COMMIT",
             "config": {"effect_type": "billing.refund", "limit_ref": "root.capability.cumulative_amount_limit"}},
            {"key": "unique_operations", "name": "Each logical operation appears once", "phase": "PRE_COMMIT",
             "expression_type": "unique_operation_keys", "failure_action": "BLOCK_COMMIT"},
        ]]
        return self._finish(inp, registry, issues, compiled, invariants, budgets, approval,
                            f"remediation/customer:{cid}/incident:{incident}", [])


# =========================================================================== disposable Git sandbox


class SandboxChangeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,40}$")
    base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")


class CodeSandboxChange(Workflow):
    key = "code_sandbox_change"
    version = "1.0.0"
    policy_version = "code-sandbox-policy/1"
    title = "Reviewed disposable sandbox change"
    description = "Promote one reviewed candidate to a dedicated disposable Git ref."
    params_model = SandboxChangeParams
    slots = (SlotSpec("promote_candidate", "git.sandbox_promote"),)

    def root_capability(self, params: dict[str, Any], grant: dict[str, Any], now: datetime) -> dict[str, Any]:
        if params["repo_id"] not in grant.get("allowed_repos", []):
            raise ValidationFailed("repository is not granted to this principal", code="REPOSITORY_NOT_GRANTED")
        return {"allowed_effect_types": ["git.sandbox_promote"],
                "allowed_resources": [f"git/repo:{params['repo_id']}/ref:pact-sandbox"],
                "amount_limit": "0.00", "cumulative_amount_limit": "0.00",
                "delegation_depth": 0,
                "expires_at": now + timedelta(hours=float(grant.get("ttl_hours", 2)))}

    def compile(self, inp: CompileInput, registry: EffectRegistry) -> CompileResult:
        issues: list[Issue] = []
        bound = self._bind_slots(inp, issues)
        proposed = bound["promote_candidate"]
        if len(proposed) != 1:
            issues.append(Issue("MISSING_REQUIRED_EFFECT", "exactly one candidate is required",
                                slot="promote_candidate"))
        compiled: list[CompiledEffect] = []
        for e in proposed:
            if (e.payload.get("repo_id") != inp.params["repo_id"]
                    or e.payload.get("base_commit") != inp.params["base_commit"]):
                issues.append(Issue("CANDIDATE_TARGET_MISMATCH", "candidate must match the trusted request",
                                    effect_id=str(e.id)))
                continue
            c = _base_effect(e, self.slots[0], registry, issues)
            if c:
                if not all(c.payload.get(k) for k in ("candidate_commit", "candidate_tree", "evidence_digest")):
                    issues.append(Issue("CANDIDATE_NOT_PREPARED", "candidate lacks frozen test evidence",
                                        effect_id=str(e.id)))
                compiled.append(c)
        return self._finish(inp, registry, issues, compiled, [], [],
                            {"required": True, "role": "code_approver",
                             "reason": "sandbox ref promotion requires review"},
                            f"code-sandbox/repo:{inp.params['repo_id']}/base:{inp.params['base_commit']}", [])


# =========================================================================== registry


class WorkflowRegistry:
    def __init__(self, workflows: list[Workflow]):
        self._w = {w.key: w for w in workflows}

    def get(self, key: str) -> Workflow:
        if key not in self._w:
            raise ValidationFailed(f"unknown workflow {key!r}", code="UNKNOWN_WORKFLOW",
                                   details={"available": sorted(self._w)})
        return self._w[key]

    def all(self) -> list[Workflow]:
        return [self._w[k] for k in sorted(self._w)]

    def register(self, wf: Workflow) -> None:
        self._w[wf.key] = wf


def default_workflows() -> WorkflowRegistry:
    return WorkflowRegistry([CustomerOffboarding(), CustomerRemediation()])

