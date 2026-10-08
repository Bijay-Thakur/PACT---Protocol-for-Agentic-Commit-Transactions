"""External-process PACT conformance fixture using only Python's HTTP library.

Run only against a disposable reference service and synthetic customer.  The
process has PACT credentials, never provider credentials or database access.
"""

from __future__ import annotations

import json
import os
import sys
import time
import http.cookiejar
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("PACT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
REQUESTER_KEY = os.environ.get("PACT_REQUESTER_API_KEY", "")
APPROVER_KEY = os.environ.get("PACT_APPROVER_API_KEY", "")


class Client:
    def __init__(self, key: str = ""):
        self.key = key
        self.csrf = ""
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def call(self, method: str, path: str, body: dict | None = None,
             request_id: str | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        if self.csrf and method != "GET":
            headers["X-PACT-CSRF"] = self.csrf
        if request_id:
            headers["X-PACT-Request-ID"] = request_id
        request = urllib.request.Request(
            f"{BASE}{path}", method=method, headers=headers,
            data=None if body is None else json.dumps(body).encode("utf-8"),
        )
        try:
            with self.opener.open(request, timeout=20) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read() or b"{}")
            raise RuntimeError(f"{method} {path}: HTTP {exc.code} {payload}") from None

    def login(self, tenant: str, username: str, password: str) -> None:
        result = self.call("POST", "/api/v1/auth/login", {
            "tenant_id": tenant, "username": username, "password": password,
        })
        self.csrf = result["csrf_token"]


def main() -> int:
    requester = Client(REQUESTER_KEY)
    approver = Client(APPROVER_KEY)
    if not REQUESTER_KEY or not APPROVER_KEY:
        tenant = os.environ.get("PACT_TENANT_ID", "")
        password = os.environ.get("PACT_FIXTURE_PASSWORD", "")
        if not tenant or not password:
            raise SystemExit(
                "supply both API keys, or PACT_TENANT_ID and PACT_FIXTURE_PASSWORD"
            )
        requester.login(tenant, os.environ.get("PACT_REQUESTER_USERNAME", "requester"), password)
        approver.login(tenant, os.environ.get("PACT_APPROVER_USERNAME", "approver"), password)
    customer = os.environ.get("PACT_SYNTHETIC_CUSTOMER_ID", "C-RAW-HTTP")
    intent = f"Cancel customer {customer} and refund the full eligible unused period"
    proposed = requester.call("POST", "/api/v1/planner/propose", {"intent": intent})
    reviewed = requester.call("POST", "/api/v1/planner/review", {
        "proposal_trace_id": proposed["proposal_trace_id"],
        "proposal": proposed["proposed_plan"],
    })
    if reviewed["status"] != "REVIEWABLE_REQUEST":
        raise RuntimeError(f"fixture requires clarification: {reviewed['issues']}")
    accepted = requester.call("POST", "/api/v1/planner/accept", {
        "proposal_trace_id": proposed["proposal_trace_id"],
        "business_request": {"customer_id": customer},
        "clarified_objective": intent,
        "clarification_note": "Synthetic conformance fixture",
        "resolved_issue_codes": [],
    }, request_id=str(uuid.uuid4()))
    root = accepted["transaction_id"]
    requester.call("POST", f"/api/v1/planner/assemble/{root}")
    frozen = requester.call("POST", f"/api/v1/transactions/{root}/prepare")
    if frozen["status"] != "FROZEN":
        raise RuntimeError(f"prepare did not freeze: {frozen}")
    digest = frozen["digest"]
    requester.call("POST", f"/api/v1/transactions/{root}/commit",
                   {"revision_digest": digest})
    approver.call("POST", f"/api/v1/transactions/{root}/approve", {
        "revision_digest": digest,
        "reason": "Separate synthetic conformance approver reviewed exact digest",
    })
    queued = requester.call("POST", f"/api/v1/transactions/{root}/commit",
                            {"revision_digest": digest})
    if queued["status"] != "QUEUED":
        raise RuntimeError(f"commit did not queue: {queued}")
    for _ in range(120):
        detail = requester.call("GET", f"/api/v1/transactions/{root}")
        state = detail["transaction"]["state"]
        if state in {"COMMITTED_VERIFIED", "HUMAN_REQUIRED", "COMPENSATED", "FAILED_TERMINAL"}:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("transaction did not reach an inspectable terminal/incident state")
    result = {"transaction_id": root, "state": state, "digest": digest}
    if state == "COMMITTED_VERIFIED":
        receipt = requester.call("GET", f"/api/v1/transactions/{root}/receipt")
        verified = requester.call("GET", f"/api/v1/transactions/{root}/receipt/verify")
        result.update({"receipt_sha256": receipt["sha256"], "receipt_valid": verified["valid"]})
    print(json.dumps(result, sort_keys=True))
    return 0 if state == "COMMITTED_VERIFIED" else 2


if __name__ == "__main__":
    sys.exit(main())
