"""Measure the reference boundary on a disposable Compose stack.

The untrusted agent container receives only a PACT API key. It cannot mount the
Git target, the database, or provider endpoints. A separate approver key stays
on this host. The host then reads the bare repository itself.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "reference" / "compose.yml"
REPO = ROOT / ".local" / "reference" / "sandbox.git"
RESULT = ROOT / ".local" / "reference" / "isolation-result.json"
HOST_API = "http://127.0.0.1:18080"
PROJECT = "pact-reference"


def run(cmd: list[str], *, env: dict[str, str], cwd: Path = ROOT,
        check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, env=env, text=True, encoding="utf-8",
                          errors="replace", check=check, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT)


def git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, encoding="utf-8",
                            errors="replace", check=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    return result.stdout.strip()


def remove_tree(path: Path) -> None:
    def onexc(func, target, _exc) -> None:
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if path.exists():
        shutil.rmtree(path, onexc=onexc)


def init_repo() -> str:
    source = REPO.parent / "source"
    remove_tree(REPO)
    remove_tree(source)
    source.mkdir(parents=True)
    git("init", "-b", "pact-sandbox", cwd=source)
    git("config", "user.name", "PACT reference", cwd=source)
    git("config", "user.email", "pact-reference@example.invalid", cwd=source)
    (source / "approved.txt").write_text("base\n", encoding="utf-8", newline="\n")
    git("add", "approved.txt", cwd=source)
    git("commit", "-m", "base", cwd=source)
    git("clone", "--bare", str(source), str(REPO), cwd=REPO.parent)
    content = git("--git-dir", str(REPO), "show", "refs/heads/pact-sandbox:approved.txt")
    if content != "base":
        raise RuntimeError("healthy target control failed before isolation")
    return git("--git-dir", str(REPO), "rev-parse", "refs/heads/pact-sandbox")


def compose_env(password: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PACT_REFERENCE_DB_PASSWORD"] = password
    env["GROQ_API_KEY"] = ""
    env["NEBIUS_API_KEY"] = ""
    env["PACT_MODEL_PROFILE"] = "deterministic_fixture"
    return env


def compose(env: dict[str, str], *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE), *args], env=env, check=check)


def provision(env: dict[str, str]) -> tuple[str, str]:
    script = r"""
import asyncio, json
from app.config import Settings
from app.domain.enums import PrincipalKind
from app.runtime import Runtime

async def main():
    rt = Runtime(Settings())
    try:
        tenant = "reference-isolation"
        agent = await rt.principals.upsert_principal(
            tenant, "isolated_agent", PrincipalKind.AGENT,
            ["tx:begin", "tx:propose", "tx:prepare", "tx:commit"],
            {"workflows": {"code_sandbox_change": {"allowed_repos": ["council_fixture"]}}})
        approver = await rt.principals.upsert_principal(
            tenant, "isolated_approver", PrincipalKind.OPERATOR, ["op:approve"],
            {"roles": ["code_approver"]})
        print(json.dumps({
            "agent_key": await rt.principals.issue_api_key(agent.id, label="reference-agent"),
            "approver_key": await rt.principals.issue_api_key(approver.id, label="reference-approver"),
        }))
    finally:
        await rt.stop()

asyncio.run(main())
"""
    proc = compose(env, "exec", "-T", "pact", "python", "-c", script)
    line = [item for item in proc.stdout.splitlines() if item.startswith("{")][-1]
    parsed = json.loads(line)
    return parsed["agent_key"], parsed["approver_key"]


def approve(key: str, root_id: str, digest: str) -> None:
    body = json.dumps({"revision_digest": digest,
                       "reason": "Separate approver reviewed the isolated sandbox digest"}).encode()
    request = urllib.request.Request(
        f"{HOST_API}/api/v1/transactions/{root_id}/approve", data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"approver rejected: HTTP {exc.code} {exc.read().decode(errors='replace')[:300]}") from None
    if payload.get("approved_digest") != digest:
        raise RuntimeError("approver did not bind the frozen digest")


def target_state() -> dict[str, str | int]:
    ref = git("--git-dir", str(REPO), "rev-parse", "refs/heads/pact-sandbox")
    content = git("--git-dir", str(REPO), "show", "refs/heads/pact-sandbox:approved.txt")
    count = int(git("--git-dir", str(REPO), "rev-list", "--count", "refs/heads/pact-sandbox"))
    return {"ref": ref, "approved_txt": content, "commit_count": count}


def main() -> int:
    password = secrets.token_urlsafe(18)
    env = compose_env(password)
    base = init_repo()
    before = target_state()
    agent: subprocess.Popen[str] | None = None
    try:
        up = compose(env, "up", "--build", "-d", "postgres", "simulators", "pact", check=False)
        if up.returncode:
            print(up.stdout[-4000:])
            return up.returncode
        ready = compose(env, "up", "-d", "--wait", "--wait-timeout", "240", "pact", check=False)
        if ready.returncode:
            print(ready.stdout[-4000:])
            return ready.returncode
        agent_key, approver_key = provision(env)
        agent = subprocess.Popen(
            ["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE), "--profile", "probe",
             "run", "--build", "--rm", "--no-deps", "-e", "PACT_MEDIATED=1", "-e", f"PACT_BASE_COMMIT={base}",
             "-e", f"PACT_AGENT_API_KEY={agent_key}", "agent"],
            cwd=ROOT, env=env, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        assert agent.stdout is not None
        signal: dict | None = None
        captured: list[str] = []
        for line in agent.stdout:
            captured.append(line)
            if line.startswith("PACT_ISOLATION "):
                signal = json.loads(line.removeprefix("PACT_ISOLATION ").strip())
                break
        if signal is None:
            rest = agent.stdout.read()
            agent.wait(timeout=30)
            print("".join(captured)[-4000:])
            print(rest[-2000:])
            return 1
        approve(approver_key, signal["root_id"], signal["digest"])
        rest = agent.stdout.read()
        code = agent.wait(timeout=120)
        agent_result = json.loads([line for line in ("".join(captured) + rest).splitlines()
                                   if line.startswith("{")][-1])
        after = target_state()
        outcome = {
            "healthy_target_before": before,
            "agent": {key: value for key, value in agent_result.items() if key != "mediated_digest"},
            "independent_target_after": {"approved_txt": after["approved_txt"],
                                         "commit_count": after["commit_count"],
                                         "ref_changed": after["ref"] != before["ref"]},
            "agent_exit": code,
        }
        ok = (code == 0 and before["approved_txt"] == "base" and before["commit_count"] == 1
              and after["approved_txt"] == "reviewed through isolated PACT"
              and after["commit_count"] == 2 and after["ref"] != before["ref"]
              and agent_result.get("mediated_state") == "COMMITTED_VERIFIED"
              and all(agent_result.get(key) is True for key in (
                  "agent_no_executor_secrets", "agent_no_target_or_daemon_mount",
                  "direct_provider_and_database_denied", "direct_filesystem_write_denied",
                  "pact_api_reachable")))
        outcome["status"] = "PASS" if ok else "FAIL"
        RESULT.parent.mkdir(parents=True, exist_ok=True)
        RESULT.write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": outcome["status"], "result": str(RESULT)}))
        if not ok:
            print(json.dumps(outcome, indent=2)[:4000])
        return 0 if ok else 1
    finally:
        if agent is not None and agent.poll() is None:
            agent.kill()
        compose(env, "down", "-v", "--remove-orphans", check=False)


if __name__ == "__main__":
    raise SystemExit(main())
