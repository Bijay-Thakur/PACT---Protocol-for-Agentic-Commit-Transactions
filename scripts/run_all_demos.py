"""Run every required demo scenario against a running PACT backend and report pass/fail.

    python scripts/run_all_demos.py            # PACT_API_URL defaults to http://localhost:8000
"""

from __future__ import annotations

import sys

from _pact import call, run, show


def main() -> int:
    _, body = call("GET", "/api/v1/demo/scenarios")
    keys = [s["key"] for s in body["scenarios"]]
    results = [show(run(k)) for k in keys]
    print(f"\n{sum(results)}/{len(results)} scenarios matched their expected final state")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
