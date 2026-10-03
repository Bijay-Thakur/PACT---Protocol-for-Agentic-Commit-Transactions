"""Scenario D: dropped billing response -> UNKNOWN -> reconcile (no duplicate refund).

Pauses at UNKNOWN so you can inspect it, then requests reconciliation explicitly.
"""

import sys

from _pact import call, run, show

r = run("unknown", pause_at_unknown=True)
show(r)
print("\n  The refund was applied by billing, but PACT never got the response.")
print(f"  billing calls so far: {[c for c in r['provider_calls'] if c.startswith('billing')]}")
status, body = call("POST", f"/api/v1/transactions/{r['root_id']}/reconcile")
print(f"\n  POST /reconcile -> {status} state={body.get('state')}")
_, ext = call("GET", f"/api/v1/demo/external-state/{r['customer_id']}")
refunds = ext["state"]["billing"]["refunds"]
creates = [c for c in ext["provider_calls"] if c["operation"] == "create_refund"]
print(f"  refunds at provider: {len(refunds)}   create_refund calls: {len(creates)}")
sys.exit(0 if body.get("state") == "COMMITTED_VERIFIED" and len(refunds) == 1 and len(creates) == 1 else 1)
