"""Disposable bare-Git boundary for the Code Council reference harness.

The repository path and target ref are trusted server configuration. Candidate
contents are data: this module never runs a candidate's tests or shell commands.
Only fixed, static fixture checks are supported until a genuine isolation
boundary is available. Preparation writes unreachable Git objects; promotion
changes one dedicated ref with Git's atomic compare-and-swap operation.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.policy.digest import digest

SHA = re.compile(r"^[0-9a-f]{40}$")
NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
CHECKS_VERSION = "static-fixture-checks/1"


class SandboxRejected(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail)


@dataclass(frozen=True)
class Candidate:
    base_commit: str
    commit: str
    tree: str
    evidence_digest: str
    paths: tuple[str, ...]


class GitSandbox:
    def __init__(self, repo: Path, *, allowed_paths: set[str],
                 ref: str = "refs/heads/pact-sandbox"):
        self.repo = repo.resolve(strict=True)
        if not self.repo.is_dir() or not (self.repo / "HEAD").exists():
            raise SandboxRejected("INVALID_REPOSITORY", "expected a configured bare Git repository")
        if not ref.startswith("refs/heads/pact-sandbox"):
            raise SandboxRejected("UNSAFE_REF", "only a dedicated sandbox ref is permitted")
        self.ref = ref
        self.allowed_paths = frozenset(self._path(p) for p in allowed_paths)

    def _git(self, *args: str, data: bytes | None = None, check: bool = True) -> str:
        env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "PACT sandbox",
               "GIT_AUTHOR_EMAIL": "pact-sandbox@example.invalid", "GIT_COMMITTER_NAME": "PACT sandbox",
               "GIT_COMMITTER_EMAIL": "pact-sandbox@example.invalid"}
        proc = subprocess.run(["git", "--git-dir", str(self.repo), *args], input=data,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=env, timeout=15, check=False)
        if check and proc.returncode:
            raise SandboxRejected("GIT_FAILURE", proc.stderr.decode(errors="replace")[:300])
        return proc.stdout.decode().strip()

    @staticmethod
    def _path(raw: str) -> str:
        p = PurePosixPath(raw)
        if (not raw or raw.startswith("/") or "\\" in raw or ":" in raw
                or any(part in {".", "..", ".git"} or not NAME.fullmatch(part)
                       for part in raw.split("/")) or str(p) != raw):
            raise SandboxRejected("UNSAFE_PATH", f"path outside the approved tree: {raw!r}")
        return raw

    def head(self) -> str:
        value = self._git("rev-parse", "--verify", self.ref)
        if not SHA.fullmatch(value):
            raise SandboxRejected("INVALID_REF", "sandbox ref does not resolve to a commit")
        return value

    def _entries(self, commit: str) -> dict[str, tuple[str, str]]:
        if not SHA.fullmatch(commit):
            raise SandboxRejected("INVALID_BASE", "base must be a full commit hash")
        raw = subprocess.run(["git", "--git-dir", str(self.repo), "ls-tree", "-rz", commit],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=15, check=False)
        if raw.returncode:
            raise SandboxRejected("INVALID_BASE", "base commit is absent")
        entries: dict[str, tuple[str, str]] = {}
        for record in raw.stdout.split(b"\0"):
            if not record:
                continue
            meta, path = record.split(b"\t", 1)
            mode, kind, oid = meta.decode().split(" ")
            entries[path.decode()] = (mode, oid)
            if kind != "blob":
                raise SandboxRejected("UNSUPPORTED_TREE", "repository has unsupported entries")
        return entries

    def prepare(self, base_commit: str, files: dict[str, str]) -> Candidate:
        if self.head() != base_commit:
            raise SandboxRejected("STALE_BASE", "sandbox ref changed since proposal")
        if not files or len(files) > 20:
            raise SandboxRejected("INVALID_BUNDLE", "bundle must contain 1 to 20 files")
        entries = self._entries(base_commit)
        for raw_path, content in files.items():
            path = self._path(raw_path)
            if path not in self.allowed_paths:
                raise SandboxRejected("PATH_NOT_ALLOWED", f"{path} is not allowlisted")
            if not isinstance(content, str) or len(content.encode()) > 100_000:
                raise SandboxRejected("INVALID_CONTENT", "file content must be UTF-8 text under 100 KB")
            if any(marker in content for marker in ("<<<<<<<", "=======", ">>>>>>>")):
                raise SandboxRejected("CONFLICT_MARKER", "unresolved conflict marker")
            for parent in PurePosixPath(path).parents:
                if str(parent) in entries:
                    raise SandboxRejected("PATH_COLLISION", "parent is a file or symlink")
            if entries.get(path, ("", ""))[0] == "120000":
                raise SandboxRejected("SYMLINK_TARGET", "symbolic links cannot be patched")
            oid = self._git("hash-object", "-w", "--stdin", data=content.encode())
            entries[path] = ("100644", oid)
        tree = self._tree(entries)
        evidence = digest({"tree": tree, "checks": CHECKS_VERSION,
                           "paths": sorted(files), "result": "PASS"})
        commit = self._git("commit-tree", tree, "-p", base_commit,
                           data=f"PACT sandbox candidate {evidence[:12]}\n".encode())
        return Candidate(base_commit, commit, tree, evidence, tuple(sorted(files)))

    def _tree(self, entries: dict[str, tuple[str, str]]) -> str:
        def build(prefix: str) -> str:
            children: dict[str, tuple[str, str]] = {}
            dirs: set[str] = set()
            for path, value in entries.items():
                if not path.startswith(prefix):
                    continue
                rest = path[len(prefix):]
                if "/" in rest:
                    dirs.add(rest.split("/", 1)[0])
                else:
                    children[rest] = value
            for name in dirs:
                children[name] = ("040000", build(f"{prefix}{name}/"))
            body = b"".join(f"{mode} {'tree' if mode == '040000' else 'blob'} {oid}\t{name}".encode() + b"\0"
                            for name, (mode, oid) in sorted(children.items()))
            return self._git("mktree", "-z", data=body)
        return build("")

    def promote(self, candidate: Candidate, evidence_digest: str) -> str:
        if evidence_digest != candidate.evidence_digest:
            raise SandboxRejected("STALE_EVIDENCE", "test evidence does not bind this candidate tree")
        expected_evidence = digest({"tree": candidate.tree, "checks": CHECKS_VERSION,
                                    "paths": sorted(candidate.paths), "result": "PASS"})
        if evidence_digest != expected_evidence:
            raise SandboxRejected("STALE_EVIDENCE", "evidence does not match the candidate tree")
        if self._git("rev-parse", f"{candidate.commit}^{{tree}}") != candidate.tree:
            raise SandboxRejected("CHANGED_CANDIDATE", "candidate tree differs from prepared digest")
        if self._git("rev-parse", f"{candidate.commit}^") != candidate.base_commit:
            raise SandboxRejected("CHANGED_BASE", "candidate parent differs from frozen base")
        actual = self.head()
        if actual == candidate.commit:
            return "ALREADY_APPLIED"
        if actual != candidate.base_commit:
            raise SandboxRejected("STALE_BASE", "sandbox ref has moved")
        proc = subprocess.run(["git", "--git-dir", str(self.repo), "update-ref", self.ref,
                               candidate.commit, candidate.base_commit],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=15, check=False)
        if proc.returncode:
            raise SandboxRejected("CAS_CONFLICT", "another promotion moved the ref")
        return "APPLIED"

    def observe(self, candidate: Candidate) -> str:
        actual = self.head()
        if actual == candidate.commit:
            return "APPLIED"
        if actual == candidate.base_commit:
            return "NOT_APPLIED"
        return "OTHER_VALUE"
