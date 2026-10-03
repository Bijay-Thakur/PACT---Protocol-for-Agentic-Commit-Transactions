# PACT MVP — validation report

Environment: Windows 11, Python 3.13, PostgreSQL 18.4 (portable binaries; Docker daemon unavailable because WSL is not installed), Node 24, Next.js 16.
Backend test suite: **110 passed** against real PostgreSQL (`pytest -q`, about 31 s); without a database, 70 unit tests pass and 40 integration tests skip.

## A. What was built (mapped to the plan)

| Plan component | Module |
|---|---|
| Transaction Manager (§6.1) | `core/transaction_manager.py` (creation, delegation, proposals, every state change + event) |
| Effect Contract Registry (§6.2) | `domain/effect.py` `EffectContract`, `adapters/registry.py`, one contract per adapter |
| Authority / Capability Engine (§6.3, §10) | `core/authority_engine.py` (issuer policy, subset delegation, chain re-validation, cumulative exposure) |
| Resource Claim & Conflict Manager (§6.4) | `core/conflict_manager.py` |
| Dependency / Effect Graph (§6.5) | `core/effect_graph.py` |
| Invariant Engine (§6.6) | `core/invariant_engine.py` (7 declarative, code-backed expression types, fail-closed) |
| Commit Barrier (§6.7) | `core/commit_barrier.py` (pure; 14 check codes; machine-readable blocking reasons) |
| Effect Executor (§6.8) | `core/executor.py` (intent persisted and committed before dispatch) |
| Verification Engine (§6.9) | `core/verifier.py` (independent external reads, bounded polling) |
| Reconciliation Engine (§6.10) | `core/reconciler.py` |
| Compensation Engine (§6.11) | `core/compensator.py` |
| Receipt Generator (§6.12) | `core/receipt_generator.py`, `domain/receipt.py` (canonical JSON, SHA-256) |
| State machines (§7, §8) | `core/state_machine.py` + DB CHECK constraints |
| Coordinator / recovery | `core/coordinator.py` (prepare, barrier, ordered drive, recovery, final verification, restart recovery) |
| Persistence (§16, §22) | `persistence/models.py`, `repositories.py`, Alembic `0001` (incl. triggers) |
| Simulated systems (§13.2) | `simulators/` (separate app + state, fault injection, call log) |
| Agents (§13.3) | `agents/scripted_demo_agents.py`, `agents/client.py` (agents use REST only) |
| Model boundary (§20) | `agents/model_provider.py`, `api/planner.py` |
| Telemetry (§21) | `telemetry/tracing.py` (verified: `pact.*` spans emitted with the console exporter) |
| API (§18) | `api/*` (all listed endpoints + SSE, contracts, operator actions, approvals) |
| Console (§19) | `frontend/` (list, launcher, barrier, hierarchy, DAG, provider-vs-reality, authority, invariants, external reality, events, receipt) |

## B. Required scenario results

Every scenario runs through the public REST API with scripted agents and fresh seeded data. Each was run by `tests/test_int_scenarios.py` (twice), by `python -m app.cli scenario` (both in-process simulators and the **standalone simulator service on a separate database**), and by `scripts/run_all_demos.py` against the running server. All passed.

| Scenario | Expected | Actual | Evidence |
|---|---|---|---|
| A success | children prepare, barrier eligible, DAG order, verified, receipt | `COMMITTED_VERIFIED`; external state cancelled / revoked / churned / one $143.27 refund / one message; receipt hash verifies; root receipt embeds 5 child hashes | `test_01_successful_commit` |
| B invariant failure | barrier rejects, no billing effect, ABORTED, explained | `ABORTED`, `INVARIANT_FAILED:refund_within_authorized_amount` (observed: proposed 450, authorized 143.27); **zero** provider calls; charges unchanged | `test_02_no_effect_before_barrier` |
| C budget conflict | $11,100 > $10,000 blocked although each child valid | all 3 children reached `PREPARED`; 3 `CUMULATIVE_AUTHORITY` checks pass; `GLOBAL_BUDGET` fails (11100.00 vs 10000.00); zero provider calls | `test_04_…`, `test_concurrent_child_budget_usage` |
| D UNKNOWN | UNKNOWN, no blind retry, reconcile by key, found, verified, continue | refund `UNKNOWN` (`RESPONSE_LOST`) while the provider shows 1 refund; barrier dry-run shows `UNRESOLVED_UNKNOWN`; reconcile → `VERIFIED_SUCCESS` → `COMMITTED_VERIFIED`; attempts `[EXECUTE, RECONCILE]`; **1** `create_refund` call | `test_06_…` |
| E compensation | compensable effects compensated in safe order, verified, truthful | revoke `FAILED`; refund + notification `ABORTED` (never dispatched); CRM then subscription compensated and verified; `COMPENSATED`; receipt keeps all original effects | `test_09_…` |
| E2 partial compensation | uncompensated consequences visible | `HUMAN_REQUIRED`; receipt is a 409 draft until an operator finalizes; then `FAILED_TERMINAL` with `uncompensated_effects=[subscription.cancel]`, the operator action recorded, no "rolled back" claim; retrying compensation later reaches `COMPENSATED` | `test_09_…`, `test_operator_retry_compensation_after_partial_failure` |
| F notification ordering | confirmation only after required effects verified | refund verification needed >1 poll; notification `dispatched_at` > refund `verified_at`; POST_EXECUTION ordering invariant passes; schedule events show the notification waiting; with an UNKNOWN refund, zero notification calls | `test_08_…` |

Additional: G verification mismatch (200 without apply → `VERIFIED_FAILURE` → `COMPENSATED`), H duplicate operation (`OPERATION_ALREADY_VERIFIED`, one refund), retryable 503 (same idempotency key on attempt 2), timeout-before-apply (`SAFE_TO_RETRY` → retried → one refund), ambiguous 500 after apply (reconciled, no second call), approval policy, local authority failure.

## C. Correctness proof points

- **No direct effect bypass.** Agents hold only a REST client. The only caller of adapter `execute` is `Executor.dispatch`, which requires root `COMMITTING`, a logical operation `RESERVED` by this root at the barrier, and all dependencies `VERIFIED`. Children get `CHILD_CANNOT_COMMIT_INDEPENDENTLY`. Scenario B and C show zero provider calls.
- **Capability enforcement.** The 12-case delegation matrix rejects scope broadening, type expansion, amount escalation (including unbounded), depth, expiry and expired parents. Self-issued root authority is rejected (`ROOT_GRANT_REJECTED`). Actors are bound to transactions. Contract-required claims are merged in. The chain is re-validated at the barrier.
- **Global invariant enforcement.** Root invariants span all child effects (C, B). Evaluations are persisted with observed values per context (prepare / commit_barrier / recovery / final_verification).
- **Commit barrier.** One binding decision per commit under the aggregate lock (3 concurrent commit requests → exactly 1 accepted, 1 decision row, 1 attempt per effect).
- **Stable operation identity.** `UNIQUE(operation_key)`. Concurrent proposals from two roots share one logical operation. Two roots racing to commit it → one `COMMITTED_VERIFIED`, one `ABORTED`, one refund. The provider idempotency key is derived from the operation key and reused across retries.
- **UNKNOWN reconciliation.** Lost response, 500-after-apply, and pending-past-poll-budget all become `UNKNOWN`. The state machine forbids `UNKNOWN → DISPATCHING/FAILED`. Reconciliation distinguishes applied / not applied / indeterminate / conflicting.
- **External verification.** A separate read path; the receipt and UI show dispatch outcome and verification status side by side (timeout + verified; 200 + verification failure).
- **Compensation.** Reverse topological order, each compensation verified, failures escalate, irreversible effects never "compensated".
- **Receipt hash.** Canonicalization unit tests (key order, equivalent decimals/timezones → same digest). `/receipt/verify` recomputes. DB triggers reject UPDATE/DELETE on `receipts` and `transaction_events`; there are no mutation routes (405). Re-finalization is idempotent.
- **Restart recovery.** (1) In-process crash after the external call, then a brand-new Runtime → `COMMITTED_VERIFIED` with attempts `[EXECUTE:ABANDONED_BY_RESTART, RECONCILE:FOUND_APPLIED]`, one refund. (2) **Real process death** (`os._exit(137)` in a subprocess) followed by `python -m app.cli recover` in a new process → same result.
- **Concurrency.** Optimistic version conflicts raise `CONCURRENCY_CONFLICT`. Concurrent child delegation/proposals/prepare produce a deterministic barrier result.

## D. Remaining limitations (stated plainly)

1. **Docker not executed here.** `docker compose config` validates and the separate-simulator topology was exercised natively, but no image was built in this environment (no WSL). The Dockerfiles are untested.
2. **No real authentication.** Agent and operator identities are asserted in requests. PACT binds and checks them server-side but does not authenticate them.
3. **Sequential execution.** No parallel dispatch within a DAG level.
4. **Recovery runs at process start** (and via the CLI), not as a continuously running worker. If the backend stays down, in-flight transactions wait.
5. **Background commits** (`background=true`, used by the console's live mode) run as in-process asyncio tasks. If the process dies mid-run, startup recovery resumes them, but there is no external queue.
6. **Children mirror the root after the barrier** (ADR-0004). Per-child terminal states are therefore equal to the root's.
7. **Invariant language** is a fixed set of 7 code-backed expression types, not a general policy language (by design for the MVP).
8. **Receipts are hashed, not signed.** No multi-tenancy. Providers are simulated.
9. **Console checks:** the console was verified by production build, HTTP checks, and a headless-browser pass by the agent that built it, including the reconcile flow. The operator-action buttons and the polling fallback were not clicked through in a browser.
10. Cross-transaction resource conflicts consider only roots past the barrier. Two roots that are both merely `PREPARED` do not block each other until one commits.

## E. Model integration readiness

- Plug-in point: `backend/app/agents/model_provider.py`. Implement `PlannerProvider.propose_transaction(intent, context) -> TransactionSpec` (and optionally `ExplanationProvider`). `OpenAICompatiblePlanner` already targets any OpenAI-compatible endpoint (e.g. NVIDIA NIM serving Nemotron) via `PACT_PLANNER_PROVIDER=openai_compatible`, `PACT_PLANNER_BASE_URL`, `PACT_PLANNER_MODEL`, `PACT_PLANNER_API_KEY`.
- Independence: `POST /api/v1/planner/propose` returns a proposal and writes nothing. Creating it requires `POST /api/v1/transactions`, which applies issuer policy, delegation subset checks, payload schemas, actor binding, invariants and the barrier. `test_contracts_planner_and_explainer` shows that a planner spec with inflated authority is rejected (`ROOT_GRANT_REJECTED`) and an unmodified one still has to pass the barrier. The explainer is labelled advisory and reads, never writes, the decision. No core module imports the model provider.
