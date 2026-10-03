"""Restart recovery with a REAL process death.

1. A PACT process commits a cancellation; right after the billing provider applies
   the refund, the process calls os._exit(137) - before persisting the response.
2. A brand-new process starts, finds the in-flight transaction in PostgreSQL,
   marks the refund UNKNOWN, reconciles it by operation identity, and finishes.

Requires the backend virtualenv and PACT_DATABASE_URL (same DB as the server).
    python scripts/run_demo_restart.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
env = {**os.environ, "PACT_AUTO_RECOVER_ON_STARTUP": "false", "PACT_LOG_LEVEL": "WARNING"}


def step(cmd: str) -> list[dict]:
    p = subprocess.run([sys.executable, "-m", "app.cli", cmd], cwd=BACKEND, env=env, capture_output=True, text=True)
    print(f"$ python -m app.cli {cmd}   (exit code {p.returncode})")
    lines = [json.loads(line) for line in p.stdout.splitlines() if line.startswith("{")]
    for line in lines:
        print("   ", line)
    if not lines:
        print(p.stderr[-2000:])
    return lines


crash = step("crash-midflight")
root = next((line["root_id"] for line in crash if line.get("event") == "ROOT_CREATED"), None)
report = step("recover")
states = {r["root_id"]: r["state"] for r in report[-1]["resumed"]} if report else {}
print(f"\nroot {root} after restart: {states.get(root)}")
sys.exit(0 if states.get(root) == "COMMITTED_VERIFIED" else 1)
