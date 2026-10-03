"""Scenario E/E2: partial commit -> compensation, and compensation that cannot fully succeed."""

import sys

from _pact import call, run, show

full = run("compensation")
ok = show(full)
partial = run("compensation-failure")
ok = show(partial) and ok
status, body = call("GET", f"/api/v1/transactions/{partial['root_id']}/receipt")
print(f"\n  receipt for HUMAN_REQUIRED tx -> {status} {body.get('error', {}).get('code')} (draft only; not terminal)")
status, body = call("POST", f"/api/v1/transactions/{partial['root_id']}/operator-actions",
                    {"operator_id": "ops-demo", "action": "FINALIZE_FAILED",
                     "note": "support will reactivate the subscription manually"})
print(f"  operator FINALIZE_FAILED -> {body.get('state')}")
_, receipt = call("GET", f"/api/v1/transactions/{partial['root_id']}/receipt")
for u in receipt["payload"]["uncompensated_effects"]:
    print(f"  uncompensated: {u['effect_type']} ({u['reason']})")
print(f"  statement: {receipt['payload']['outcome_statement']}")
sys.exit(0 if ok else 1)
