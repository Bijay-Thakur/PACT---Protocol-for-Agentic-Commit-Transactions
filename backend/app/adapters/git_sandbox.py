"""Optional PACT effect adapter for an explicitly configured disposable bare Git repo."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.adapters.base import AdapterContext, unreadable
from app.domain.effect import EffectContract, EffectView
from app.domain.enums import (Application, ClaimMode, CompensationStrategy, Consistency,
                              DispatchOutcome, IdempotencyStrategy, Postcondition,
                              PrepareStrategy, ReconciliationPolicy, ReversibilityClass,
                              VerificationStrategy)
from app.domain.resource_claim import ResourceClaim
from app.domain.verification import DispatchResult, Observation, PrepareResult
from app.integrations.git_sandbox import Candidate, GitSandbox, SandboxRejected


class SandboxPatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,40}$")
    base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    files: dict[str, str] = Field(min_length=1, max_length=20)
    candidate_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    candidate_tree: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    evidence_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


CONTRACT = EffectContract(
    effect_type="git.sandbox_promote", adapter_name="git_sandbox", provider="disposable-git",
    resource_type="dedicated.sandbox_ref", operation_kind="compare_and_swap_ref",
    required_capability="git.sandbox_promote", reversibility_class=ReversibilityClass.IRREVERSIBLE,
    prepare_strategy=PrepareStrategy.READ_PRECONDITIONS,
    verification_strategy=VerificationStrategy.QUERY_TARGET_STATE,
    compensation_strategy=CompensationStrategy.NONE,
    idempotency_strategy=IdempotencyStrategy.TARGET_STATE_IDEMPOTENT,
    reconciliation_policy=ReconciliationPolicy.QUERY_TARGET_STATE,
    evidence_requirements=["base_commit", "candidate_commit", "candidate_tree", "ref"],
    consistency=Consistency.STRONG, negative_evidence="AUTHORITATIVE",
    definitive_rejection_statuses=[409, 412, 422], max_attempts=1,
    restoration_scope="none: moving a Git ref does not erase downstream observations",
    description="Atomically promote an approved candidate to one configured disposable sandbox ref.",
    payload_model=SandboxPatchPayload,
    required_claims=lambda p: [ResourceClaim(
        resource=f"git/repo:{p['repo_id']}/ref:pact-sandbox", mode=ClaimMode.EXCLUSIVE)],
)


class GitSandboxAdapter:
    contract = CONTRACT

    def __init__(self, sandbox: GitSandbox, repo_id: str):
        self.sandbox = sandbox
        self.repo_id = repo_id

    def _candidate(self, payload: dict[str, Any]) -> Candidate:
        return Candidate(payload["base_commit"], payload["candidate_commit"],
                         payload["candidate_tree"], payload["evidence_digest"],
                         tuple(sorted(payload["files"])))

    async def prepare(self, effect: EffectView, ctx: AdapterContext) -> PrepareResult:
        p = effect.payload
        if p.get("repo_id") != self.repo_id:
            return PrepareResult(ok=False, reason="repository is not the configured sandbox")
        try:
            candidate = await asyncio.to_thread(self.sandbox.prepare, p["base_commit"], p["files"])
        except SandboxRejected as exc:
            return PrepareResult(ok=False, reason=f"{exc.code}: {exc}")
        return PrepareResult(ok=True, observations={"checks": "PASS", "checks_version": "static-fixture-checks/1",
                                                    "candidate_tree": candidate.tree},
                             resolved={"candidate_commit": candidate.commit, "candidate_tree": candidate.tree,
                                       "evidence_digest": candidate.evidence_digest},
                             preconditions={"base_commit": candidate.base_commit,
                                            "candidate_tree": candidate.tree,
                                            "evidence_digest": candidate.evidence_digest},
                             provenance="DISPOSABLE_GIT_SANDBOX")

    async def freshness(self, effect: EffectView, ctx: AdapterContext) -> tuple[bool, dict[str, Any]]:
        try:
            actual = await asyncio.to_thread(self.sandbox.head)
            frozen = self._candidate(effect.payload)
            return actual == frozen.base_commit, {"frozen_base": frozen.base_commit, "current_ref": actual}
        except (SandboxRejected, KeyError, TypeError) as exc:
            return False, {"error": str(exc)}

    async def execute(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult:
        try:
            candidate = self._candidate(effect.payload)
            result = await asyncio.to_thread(self.sandbox.promote, candidate, candidate.evidence_digest)
            return DispatchResult(outcome=DispatchOutcome.ACCEPTED,
                                  provider_reference=candidate.commit,
                                  response={"result": result, "ref": self.sandbox.ref})
        except SandboxRejected as exc:
            return DispatchResult(outcome=DispatchOutcome.REJECTED_DEFINITIVE,
                                  error=f"{exc.code}: {exc}", stale_precondition=exc.code in {
                                      "STALE_BASE", "CAS_CONFLICT"})

    async def observe(self, effect: EffectView, ctx: AdapterContext) -> Observation:
        try:
            candidate = self._candidate(effect.payload)
            finding = await asyncio.to_thread(self.sandbox.observe, candidate)
            actual = await asyncio.to_thread(self.sandbox.head)
        except (SandboxRejected, KeyError, TypeError) as exc:
            return unreadable(str(exc), source="disposable-git")
        evidence = {"base_commit": candidate.base_commit, "candidate_commit": candidate.commit,
                    "candidate_tree": candidate.tree, "evidence_digest": candidate.evidence_digest,
                    "ref": self.sandbox.ref, "actual_ref": actual}
        if finding == "APPLIED":
            return Observation(application=Application.APPLIED, postcondition=Postcondition.MATCH,
                               evidence=evidence, provider_reference=candidate.commit,
                               source="disposable-git", provenance="DISPOSABLE_GIT_SANDBOX")
        if finding == "NOT_APPLIED":
            return Observation(application=Application.NOT_APPLIED_CONFIRMED,
                               postcondition=Postcondition.MISMATCH, absent=True, evidence=evidence,
                               reason="sandbox ref still points to approved base", source="disposable-git",
                               provenance="DISPOSABLE_GIT_SANDBOX")
        return Observation(application=Application.UNKNOWN, postcondition=Postcondition.UNDETERMINED,
                           evidence=evidence, reason="sandbox ref points to another value",
                           source="disposable-git", provenance="DISPOSABLE_GIT_SANDBOX")

    async def restore(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult:
        return DispatchResult(outcome=DispatchOutcome.REJECTED_DEFINITIVE,
                              error="sandbox promotion is not automatically reversible")

    async def observe_restoration(self, effect: EffectView, ctx: AdapterContext) -> Observation:
        return unreadable("not automatically reversible", source="disposable-git")
