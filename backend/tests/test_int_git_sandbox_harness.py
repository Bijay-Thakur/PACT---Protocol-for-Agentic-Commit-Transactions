"""Real disposable Git objects and ref CAS; no untrusted code execution."""

from __future__ import annotations

import subprocess
import uuid
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from app.integrations.git_sandbox import GitSandbox, SandboxRejected
from app.adapters.git_sandbox import GitSandboxAdapter
from app.domain.effect import EffectProposal
from app.domain.enums import PrincipalKind
from app.domain.enums import DispatchOutcome
from app.domain.transaction import ApprovalRequest, BeginRequest, CommitRequest
from app.domain.verification import DispatchResult
from app.policy.workflows import CodeSandboxChange
from app.worker import Worker
from app.config import Settings
from app.runtime import Runtime
from app.persistence.models import TransactionRow

from .conftest import requires_db


@pytest.fixture
async def git_rt(rt):
    """Each adapter variant gets its own trusted registration table."""
    runtime = Runtime(rt.settings)
    await runtime.start()
    try:
        yield runtime
    finally:
        await runtime.stop()


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return result.stdout.strip()


@pytest.fixture
def sandbox(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git("init", "-b", "pact-sandbox", cwd=source)
    git("config", "user.name", "PACT test", cwd=source)
    git("config", "user.email", "pact@example.invalid", cwd=source)
    (source / "approved.txt").write_text("base\n")
    git("add", "approved.txt", cwd=source)
    git("commit", "-m", "base", cwd=source)
    bare = tmp_path / "sandbox.git"
    git("clone", "--bare", str(source), str(bare), cwd=tmp_path)
    return GitSandbox(bare, allowed_paths={"approved.txt", "docs/note.txt"})


def test_candidate_evidence_and_atomic_promotion(sandbox):
    base = sandbox.head()
    candidate = sandbox.prepare(base, {"approved.txt": "fixed\n", "docs/note.txt": "reviewed\n"})
    assert sandbox.head() == base  # preparation changes only unreachable objects
    assert candidate.tree != sandbox._git("rev-parse", f"{base}^{{tree}}")
    with pytest.raises(SandboxRejected, match="evidence"):
        sandbox.promote(candidate, "0" * 64)
    assert sandbox.promote(candidate, candidate.evidence_digest) == "APPLIED"
    assert sandbox.observe(candidate) == "APPLIED"
    assert sandbox.promote(candidate, candidate.evidence_digest) == "ALREADY_APPLIED"
    assert sandbox._git("show", f"{sandbox.ref}:approved.txt") == "fixed"


def test_unsafe_paths_and_conflicting_patch_block(sandbox):
    base = sandbox.head()
    for path in ("../escape.txt", "/absolute.txt", "x/../../escape", ".git/config",
                 "docs\\note.txt", "unapproved.txt"):
        with pytest.raises(SandboxRejected):
            sandbox.prepare(base, {path: "bad\n"})
    with pytest.raises(SandboxRejected) as err:
        sandbox.prepare(base, {"approved.txt": "<<<<<<< one\n=======\n>>>>>>> two"})
    assert err.value.code == "CONFLICT_MARKER"
    assert sandbox.head() == base


def test_stale_candidate_and_evidence_change_block(sandbox):
    base = sandbox.head()
    first = sandbox.prepare(base, {"approved.txt": "first\n"})
    second = sandbox.prepare(base, {"approved.txt": "second\n"})
    assert first.tree != second.tree
    with pytest.raises(SandboxRejected) as err:
        sandbox.promote(second, first.evidence_digest)
    assert err.value.code == "STALE_EVIDENCE"
    with pytest.raises(SandboxRejected) as err:
        sandbox.promote(replace(first, tree=second.tree), first.evidence_digest)
    assert err.value.code == "STALE_EVIDENCE"
    sandbox.promote(first, first.evidence_digest)
    with pytest.raises(SandboxRejected) as err:
        sandbox.promote(second, second.evidence_digest)
    assert err.value.code == "STALE_BASE"
    with pytest.raises(SandboxRejected) as err:
        sandbox.prepare(base, {"approved.txt": "third\n"})
    assert err.value.code == "STALE_BASE"


def test_two_roots_cannot_cas_same_old_ref(sandbox):
    base = sandbox.head()
    a = sandbox.prepare(base, {"approved.txt": "a\n"})
    b = sandbox.prepare(base, {"approved.txt": "b\n"})
    def promote(candidate):
        try:
            return sandbox.promote(candidate, candidate.evidence_digest)
        except SandboxRejected as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(promote, (a, b)))
    assert outcomes.count("APPLIED") == 1, outcomes
    assert any(x in {"STALE_BASE", "CAS_CONFLICT"} for x in outcomes)


def test_lost_response_reconciles_from_actual_ref(sandbox):
    candidate = sandbox.prepare(sandbox.head(), {"approved.txt": "after loss\n"})
    sandbox.promote(candidate, candidate.evidence_digest)
    # The caller may have lost the success response; inspect Git independently.
    assert sandbox.observe(candidate) == "APPLIED"
    assert sandbox.promote(candidate, candidate.evidence_digest) == "ALREADY_APPLIED"


async def test_optional_runtime_configuration_registers_only_allowlisted_sandbox(sandbox):
    settings = Settings(code_sandbox_repo=str(sandbox.repo),
                        code_sandbox_repo_id="council_fixture",
                        code_sandbox_allowed_paths=["approved.txt", "docs/note.txt"])
    rt = Runtime(settings)
    try:
        assert rt.registry.has("git.sandbox_promote")
        assert rt.workflows.get("code_sandbox_change").key == "code_sandbox_change"
    finally:
        await rt.stop()


@requires_db
@pytest.mark.parametrize("lost_response", [False, True])
async def test_pact_compiles_approves_and_promotes_disposable_git_candidate(git_rt, sandbox, lost_response):
    class LostResponseAdapter(GitSandboxAdapter):
        async def execute(self, effect, ctx):
            result = await super().execute(effect, ctx)
            if result.outcome == DispatchOutcome.ACCEPTED:
                return DispatchResult(outcome=DispatchOutcome.RESPONSE_LOST,
                                      error="simulated response loss after Git update-ref")
            return result
    adapter_class = LostResponseAdapter if lost_response else GitSandboxAdapter
    rt = git_rt
    rt.registry.register(adapter_class(sandbox, "council_fixture"))
    rt.workflows.register(CodeSandboxChange())
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    agent = await rt.principals.upsert_principal(
        tenant, "council_agent", PrincipalKind.AGENT,
        ["tx:begin", "tx:propose", "tx:prepare", "tx:commit"],
        {"workflows": {"code_sandbox_change": {"allowed_repos": ["council_fixture"]}}})
    approver = await rt.principals.upsert_principal(
        tenant, "reviewer", PrincipalKind.OPERATOR, ["op:approve"],
        {"roles": ["code_approver"]})
    base = sandbox.head()
    root = await rt.manager.begin(agent, BeginRequest(
        workflow="code_sandbox_change",
        business_request={"repo_id": "council_fixture", "base_commit": base}))
    await rt.manager.propose(agent, root, EffectProposal(
        effect_type="git.sandbox_promote", slot="promote_candidate",
        payload={"repo_id": "council_fixture", "base_commit": base,
                 "files": {"approved.txt": "reviewed through PACT\n"}}))
    frozen = await rt.coordinator.prepare(agent, root)
    assert frozen["status"] == "FROZEN", frozen
    assert sandbox.head() == base
    waiting = await rt.coordinator.request_commit(agent, root,
        CommitRequest(revision_digest=frozen["digest"]))
    assert waiting["status"] == "AWAITING_APPROVAL", waiting
    await rt.coordinator.approve(approver, root, ApprovalRequest(
        revision_digest=frozen["digest"], reason="Reviewed disposable sandbox change"))
    queued = await rt.coordinator.request_commit(agent, root,
        CommitRequest(revision_digest=frozen["digest"]))
    assert queued["status"] == "QUEUED", queued
    # Rebuild the runtime from its database and trusted sandbox configuration.
    # The work item was enqueued by rt; this fresh instance claims and finishes it.
    resumed = Runtime(rt.settings)
    resumed.registry.register(adapter_class(sandbox, "council_fixture"))
    resumed.workflows.register(CodeSandboxChange())
    await resumed.start()
    try:
        worker = Worker(resumed)
        for _ in range(80):
            await worker.run_once()
            async with resumed.db.read() as session:
                current = await session.get(TransactionRow, root)
                if current.state == "COMMITTED_VERIFIED":
                    break
            await asyncio.sleep(0.05)
        async with resumed.db.read() as session:
            tx = await session.get(TransactionRow, root)
            assert tx.state == "COMMITTED_VERIFIED", tx.state
    finally:
        await resumed.stop()
    assert sandbox.head() != base
    assert sandbox._git("show", f"{sandbox.ref}:approved.txt") == "reviewed through PACT"
