"""Run inside the restricted agent container; exit nonzero on a bypass."""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from urllib.request import Request, urlopen


def unreachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return False
    except (OSError, TimeoutError):
        return True


def main() -> int:
    forbidden_env = [key for key in os.environ if key.startswith((
        "PACT_DATABASE", "PACT_SIM_DATABASE", "GROQ_API_KEY", "NEBIUS_API_KEY"))]
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
              "pact_api_reachable": api_reachable}
    print(json.dumps(result))
    return 0 if all(result.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
