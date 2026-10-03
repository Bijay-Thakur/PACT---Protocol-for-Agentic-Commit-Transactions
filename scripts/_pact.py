"""Tiny stdlib-only client for the PACT demo scripts (no third-party dependencies)."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

API = os.environ.get("PACT_API_URL", "http://localhost:8000").rstrip("/")


def call(method: str, path: str, body: Any = None, timeout: float = 180) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")
    except urllib.error.URLError as exc:
        sys.exit(f"cannot reach PACT at {API}: {exc.reason} (start the backend or set PACT_API_URL)")


def run(scenario: str, **opts: Any) -> dict[str, Any]:
    status, body = call("POST", f"/api/v1/demo/run/{scenario}", opts)
    if status != 200:
        sys.exit(f"{scenario}: HTTP {status} {body}")
    return body


def show(result: dict[str, Any]) -> bool:
    ok = result.get("matches_expected")
    mark = "PASS" if ok else ("...." if ok is None else "FAIL")
    print(f"[{mark}] {result['title']}")
    print(f"       root={result['root_id']} customer={result['customer_id']}")
    print(f"       state={result['state']} expected={result['expected_state']}")
    reasons = (result.get("commit_decision") or {}).get("blocking_reasons")
    if reasons:
        print(f"       barrier blocked: {', '.join(reasons)}")
    print(f"       provider calls: {', '.join(result.get('provider_calls') or []) or '(none - nothing left PACT)'}")
    print(f"       console: http://localhost:3000/tx/{result['root_id']}")
    return bool(ok) or ok is None
