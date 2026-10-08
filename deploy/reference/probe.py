"""Run inside the restricted agent container; exit nonzero on a bypass."""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
from pathlib import Path
from urllib.request import Request, urlopen


def unreachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return False
    except (OSError, TimeoutError):
        return True


def direct_write_denied() -> bool:
    target = Path("/srv/target/bypass.txt")
    try:
        target.write_text("bypass")
    except OSError:
        return True
    return False


def api(method: str, path: str, body: dict | None = None) -> dict:
    key = os.environ["PACT_AGENT_API_KEY"]
    request = Request(
        os.environ["PACT_API_URL"].rstrip("/") + path,
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode(errors="replace")[:500]
        raise RuntimeError(f"{method} {path}: HTTP {exc.code} {payload}") from None


def mediated(result: dict) -> None:
    """Ask PACT to promote one file. This process never receives the Git mount."""
    base = os.environ["PACT_BASE_COMMIT"]
    created = api("POST", "/api/v1/transactions", {
        "workflow": "code_sandbox_change",
        "business_request": {"repo_id": "council_fixture", "base_commit": base},
    })
    root = created["transaction_id"]
    api("POST", f"/api/v1/transactions/{root}/effects", {
        "effect_type": "git.sandbox_promote",
        "slot": "promote_candidate",
        "payload": {"repo_id": "council_fixture", "base_commit": base,
                    "files": {"approved.txt": "reviewed through isolated PACT\n"}},
    })
    frozen = api("POST", f"/api/v1/transactions/{root}/prepare")
    if frozen.get("status") != "FROZEN" or not frozen.get("digest"):
        raise RuntimeError(f"prepare did not freeze: {frozen}")
    digest = frozen["digest"]
    waiting = api("POST", f"/api/v1/transactions/{root}/commit", {"revision_digest": digest})
    if waiting.get("status") != "AWAITING_APPROVAL":
        raise RuntimeError(f"commit did not wait for a separate approver: {waiting}")
    print("PACT_ISOLATION " + json.dumps({
        "phase": "awaiting_approval", "root_id": root, "digest": digest}), flush=True)
    queued = waiting
    for _ in range(90):
        if queued.get("status") == "QUEUED":
            break
        time.sleep(1)
        queued = api("POST", f"/api/v1/transactions/{root}/commit", {"revision_digest": digest})
    if queued.get("status") != "QUEUED":
        raise RuntimeError(f"approval was not accepted: {queued}")
    state = ""
    detail: dict = {}
    for _ in range(60):
        detail = api("GET", f"/api/v1/transactions/{root}")
        transaction = detail.get("transaction") if isinstance(detail.get("transaction"), dict) else {}
        state = str(transaction.get("state") or detail.get("state") or "")
        if state in {"COMMITTED_VERIFIED", "HUMAN_REQUIRED", "FAILED", "ABORTED"}:
            break
        time.sleep(1)
    result["mediated_root_id"] = root
    result["mediated_state"] = state
    result["mediated_digest"] = digest
    if state != "COMMITTED_VERIFIED":
        raise RuntimeError(f"mediated promotion ended {state}: {detail.get('state')}")


def main() -> int:
    forbidden_env = [key for key in os.environ if key.startswith((
        "PACT_DATABASE", "PACT_SIM_DATABASE", "GROQ_API_KEY", "NEBIUS_API_KEY",
        "PACT_REFERENCE_DB_PASSWORD"))]
    forbidden_paths = [p for p in ("/srv/target", "/var/run/docker.sock", "/run/secrets") if Path(p).exists()]
    direct_network_denied = unreachable("postgres", 5432) and unreachable("simulators", 8100)
    api_reachable = False
    try:
        with urlopen(Request(os.environ["PACT_API_URL"] + "/healthz"), timeout=5) as response:
            api_reachable = response.status == 200
    except OSError:
        pass
    result = {"agent_no_executor_secrets": not forbidden_env,
              "agent_no_target_or_daemon_mount": not forbidden_paths,
              "direct_provider_and_database_denied": direct_network_denied,
              "direct_filesystem_write_denied": direct_write_denied(),
              "pact_api_reachable": api_reachable}
    try:
        if os.environ.get("PACT_MEDIATED") == "1":
            mediated(result)
    except Exception as exc:
        result["mediated_error"] = str(exc)[:500]
    print(json.dumps(result), flush=True)
    required = [value for key, value in result.items() if key != "mediated_error" and isinstance(value, bool)]
    mediated_ok = os.environ.get("PACT_MEDIATED") != "1" or result.get("mediated_state") == "COMMITTED_VERIFIED"
    return 0 if all(required) and mediated_ok and "mediated_error" not in result else 1


if __name__ == "__main__":
    raise SystemExit(main())
