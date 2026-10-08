"""Explicit wiring of the PACT runtime (no hidden global state).

A :class:`Runtime` owns the database, the adapter HTTP client, the registry,
the engines and the coordinator. The FastAPI app keeps one on ``app.state``;
tests and the CLI build their own - which is also how restart recovery is
exercised: a brand-new Runtime resumes purely from PostgreSQL.
"""

from __future__ import annotations

import httpx
from pathlib import Path

from app.adapters.registry import EffectRegistry, default_registry
from app.agents.model_provider import DeterministicExplainer, build_planner
from app.agents.semantic_judge import build_judge
from app.config import Settings
from app.core.authority_engine import AuthorityEngine
from app.core.commit_barrier import CommitBarrier
from app.core.coordinator import Coordinator
from app.core.executor import CrashHook, ExecutionContext
from app.core.invariant_engine import InvariantEngine
from app.core.receipt_generator import ReceiptGenerator
from app.core.evidence import EvidenceLedger
from app.core.reservations import BudgetService, ReservationService
from app.core.transaction_manager import TransactionManager
from app.core.work_queue import WorkQueue
from app.persistence.db import Database
from app.policy.workflows import CodeSandboxChange, default_workflows
from app.policy.assemblers import AssemblyRegistry, OffboardingAssembler
from app.security.principals import PrincipalService
from app.simulators.app import create_sim_app
from app.simulators.store import SimStore


class Runtime:
    def __init__(self, settings: Settings, *, registry: EffectRegistry | None = None,
                 crash_hook: CrashHook | None = None, pool_size: int = 10):
        self.settings = settings
        self.db = Database(settings.database_url, pool_size=pool_size)
        self.sim_store: SimStore | None = None
        self.sim_app = None
        if settings.sim_base_url == "inprocess":
            sim_db = self.db if settings.effective_sim_database_url == settings.database_url else Database(
                settings.effective_sim_database_url, pool_size=5)
            self.sim_store = SimStore(sim_db)
            self.sim_app = create_sim_app(self.sim_store)
            transport = httpx.ASGITransport(app=self.sim_app)
            self.http = httpx.AsyncClient(transport=transport, base_url="http://simulated-providers")
        else:
            self.http = httpx.AsyncClient(base_url=settings.sim_base_url)
        self.registry = registry or default_registry()
        self.workflows = default_workflows()
        self.assemblers = AssemblyRegistry([OffboardingAssembler()])
        if settings.code_sandbox_repo:
            if not settings.code_sandbox_repo_id or not settings.code_sandbox_allowed_paths:
                raise ValueError("Git sandbox requires repo ID and explicit allowed paths")
            from app.adapters.git_sandbox import GitSandboxAdapter
            from app.integrations.git_sandbox import GitSandbox
            sandbox = GitSandbox(Path(settings.code_sandbox_repo),
                                 allowed_paths=set(settings.code_sandbox_allowed_paths))
            self.registry.register(GitSandboxAdapter(sandbox, settings.code_sandbox_repo_id))
            self.workflows.register(CodeSandboxChange())
        self.principals = PrincipalService(self.db)
        self.budgets = BudgetService()
        self.reservations = ReservationService()
        self.queue = WorkQueue(self.db, lease_s=settings.worker_lease_s)
        self.evidence = EvidenceLedger(self.budgets)
        self.authority = AuthorityEngine(settings)
        self.invariants = InvariantEngine()
        self.manager = TransactionManager(self.db, self.registry, self.authority, self.workflows, self.principals)
        self.barrier = CommitBarrier(self.registry, self.authority, self.invariants)
        self.receipts = ReceiptGenerator(self.registry)
        self.exec_ctx = ExecutionContext(self.db, self.manager, self.registry, self.http,
                                         settings.adapter_timeout_s, self.queue, self.evidence, crash_hook)
        self.judge = build_judge(settings)
        self.coordinator = Coordinator(self.exec_ctx, self.manager, self.barrier, self.invariants,
                                       self.receipts, self.workflows, self.reservations, self.budgets, settings,
                                       judge=self.judge)
        self.planner = build_planner(settings, self.workflows)
        self.explainer = DeterministicExplainer()

    async def start(self) -> None:
        if self.sim_store is not None:
            await self.sim_store.create_schema()

    async def stop(self) -> None:
        await self.http.aclose()
        await self.db.dispose()
        if self.sim_store is not None and self.sim_store.db is not self.db:
            await self.sim_store.db.dispose()
