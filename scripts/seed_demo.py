"""Populate the console with one run of every scenario (deterministic seeded customer data)."""

from _pact import call, run

_, body = call("GET", "/api/v1/demo/scenarios")
for s in body["scenarios"]:
    r = run(s["key"])
    print(f"{s['key']:<24} {r['state']:<20} {r['root_id']}")
