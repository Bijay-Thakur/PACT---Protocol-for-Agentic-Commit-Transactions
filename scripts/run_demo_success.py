"""Scenario A: successful global commit, then verify the receipt hash."""

import sys

from _pact import call, run, show

r = run("success")
ok = show(r)
_, v = call("GET", f"/api/v1/transactions/{r['root_id']}/receipt/verify")
print(f"       receipt sha256={v['stored_sha256']} valid={v['valid']}")
sys.exit(0 if ok and v["valid"] else 1)
