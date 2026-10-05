"""Runtime configuration from environment variables (no secrets in source)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _bool(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").lower() in ("1", "true", "yes", "on")


DEFAULT_ISSUER_POLICIES = {
    # issuer -> maximum authority it may grant to a root transaction.
    "operator:demo": {"max_amount": "50000", "max_delegation_depth": 3},
    "operator:finance-ops": {"max_amount": "250000", "max_delegation_depth": 4},
}


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: _env(
            "PACT_DATABASE_URL", "postgresql+asyncpg://pact:pact@localhost:5432/pact"
        )
    )
    # "inprocess" mounts the simulated external systems via an in-process ASGI
    # transport; any http(s) URL targets a separately running simulator service.
    sim_base_url: str = field(default_factory=lambda: _env("PACT_SIM_BASE_URL", "inprocess"))
    sim_database_url: str = field(default_factory=lambda: _env("PACT_SIM_DATABASE_URL", ""))
    adapter_timeout_s: float = field(
        default_factory=lambda: float(_env("PACT_ADAPTER_TIMEOUT_S", "1.0"))
    )
    auto_recover_on_startup: bool = field(
        default_factory=lambda: _bool("PACT_AUTO_RECOVER_ON_STARTUP", True)
    )
    cors_origins: list[str] = field(
        default_factory=lambda: _env(
            "PACT_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
        ).split(",")
    )
    otel_exporter: str = field(default_factory=lambda: _env("PACT_OTEL_EXPORTER", "none"))
    log_level: str = field(default_factory=lambda: _env("PACT_LOG_LEVEL", "INFO"))
    issuer_policies: dict = field(
        default_factory=lambda: json.loads(
            _env("PACT_ISSUER_POLICIES", json.dumps(DEFAULT_ISSUER_POLICIES))
        )
    )
    planner_provider: str = field(default_factory=lambda: _env("PACT_PLANNER_PROVIDER", "deterministic"))
    planner_base_url: str = field(default_factory=lambda: _env("PACT_PLANNER_BASE_URL", ""))
    planner_model: str = field(default_factory=lambda: _env("PACT_PLANNER_MODEL", ""))
    planner_api_key: str = field(default_factory=lambda: _env("PACT_PLANNER_API_KEY", ""))
    planner_share_workflow_catalog: bool = field(
        default_factory=lambda: _bool("PACT_PLANNER_SHARE_WORKFLOW_CATALOG", False))
    worker_lease_s: float = field(default_factory=lambda: float(_env("PACT_WORKER_LEASE_S", "15")))
    approval_ttl_minutes: float = field(default_factory=lambda: float(_env("PACT_APPROVAL_TTL_MINUTES", "30")))
    demo_mode: bool = field(default_factory=lambda: _bool("PACT_DEMO_MODE", False))
    embedded_worker: bool = field(default_factory=lambda: _bool("PACT_EMBEDDED_WORKER", True))
    code_sandbox_repo: str = field(default_factory=lambda: _env("PACT_CODE_SANDBOX_REPO", ""))
    code_sandbox_repo_id: str = field(default_factory=lambda: _env("PACT_CODE_SANDBOX_REPO_ID", ""))
    code_sandbox_allowed_paths: list[str] = field(default_factory=lambda: [
        p for p in _env("PACT_CODE_SANDBOX_ALLOWED_PATHS", "").split(",") if p])

    def issuer_max_amount(self, issuer: str) -> Decimal | None:
        policy = self.issuer_policies.get(issuer)
        return Decimal(str(policy["max_amount"])) if policy else None

    @property
    def effective_sim_database_url(self) -> str:
        return self.sim_database_url or self.database_url
