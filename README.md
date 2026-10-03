# PACT — Protocol for Agentic Commit Transactions

**PACT is the transaction and commit layer that lets autonomous agents coordinate real-world side effects across multiple systems under shared authority and invariants, verify what actually happened, recover safely from ambiguity or partial failure, and produce an accountable receipt of the final business outcome.**

> Agents may reason, delegate and propose effects independently, but effects that belong to the same business transaction may not commit independently.

## Why this is not something you already have

```text
LangSmith / Phoenix -> observe and evaluate what agents did
MCP                 -> expose capabilities to agents
Temporal            -> durable workflow execution
Rollback / Saga     -> compensate failures after the fact
PACT                -> coordinate whether distributed agent effects may commit together,
                       verify the real outcome, and produce accountable transaction evidence
```

- **Not a tracing dashboard.** PACT *decides*: a deterministic global commit barrier evaluates authority, delegated budgets, resource conflicts, cross-agent invariants and idempotency over one consistent snapshot before anything touches an external system. It emits OpenTelemetry so tracing tools can consume it.
- **Not rollback.** Compensation is one recovery path. PACT acts before execution (barrier), during execution (verification-gated ordering), and after (UNKNOWN reconciliation, compensation, escalation). It never claims atomicity over APIs that can't provide it: irreversible effects are declared as such, and a transaction that cannot be fully restituted ends `HUMAN_REQUIRED`, not "rolled back".
- **Not a workflow engine.** PACT determines whether execution is *semantically permitted to commit*. A durable workflow runtime could host its coordinator later (ADR-0006).
- **Not MCP.** Any MCP tool can become a PACT effect by adding an adapter and contract. The MCP server itself is unchanged.

## What the MVP demonstrates

Golden objective: *"Cancel customer C-48291, refund the unused subscription period up to the authorized amount, revoke premium access, update the CRM record, and send confirmation only after the required business effects are verified."* A root agent delegates narrow capabilities to five child agents (billing, subscription, identity, CRM, notification). They propose effects against five simulated providers, each with its own independently queryable state.

| # | Scenario | What PACT does | Final state |
|---|---|---|---|
| A | `success` | children prepare → barrier passes → DAG executes → every effect independently verified → receipt | `COMMITTED_VERIFIED` |
| B | `invariant-failure` | $450 refund is within the agent's own cap but above the $143.27 unused balance → barrier blocks; zero provider calls | `ABORTED` |
| C | `budget-conflict` | 3 agents, each within a local $6,000 cap ($1,800 + $3,800 + $5,500), exceed the $10,000 root authority → `GLOBAL_BUDGET_EXCEEDED` | `ABORTED` |
| D | `unknown` | billing applies the refund but the response is lost → `UNKNOWN`, **no retry**, reconcile by operation identity, refund found, continue | `COMMITTED_VERIFIED` (one refund) |
| E | `compensation` | entitlement revoke rejected → never-dispatched refund/notification released → CRM + subscription compensated in reverse order and verified | `COMPENSATED` |
| E2 | `compensation-failure` | as E, but reactivation fails → no false "rollback" | `HUMAN_REQUIRED` |
| F | `notification-ordering` | refund invisible for several reads → notification waits until the refund is *verified* | `COMMITTED_VERIFIED` |
| G | `verification-mismatch` | CRM returns 200 without applying → verification catches it → compensate | `COMPENSATED` |
| H | `duplicate-operation` | another run proposes the same refund `operation_key` → `OPERATION_ALREADY_VERIFIED` | `ABORTED` (one refund) |

Restart recovery is demonstrated with a real process death: `os._exit(137)` right after billing applies the refund, then a fresh process reconciles from PostgreSQL with no duplicate refund.

## Quick start

### Docker (intended)

```bash
cp .env.example .env
docker compose up --build
# console:  http://localhost:3000     API docs: http://localhost:8000/docs
python scripts/run_all_demos.py      # runs every scenario against the API (stdlib only)
```

Compose runs `postgres`, `simulators` (the external systems, separate DB `pact_sim`), `backend` (runs migrations on start) and `frontend`.

### Without Docker

Requirements: Python 3.12+, Node 20+, PostgreSQL 14+.

```bash
python -m venv .venv && .venv/bin/pip install -e "backend[dev]"      # Windows: .venv\Scripts\pip
export PACT_DATABASE_URL=postgresql+asyncpg://pact:pact@localhost:5432/pact
cd backend && alembic upgrade head && uvicorn app.main:app --port 8000
# new terminal
cd frontend && npm install && npm run dev                            # http://localhost:3000
```

With `PACT_SIM_BASE_URL=inprocess` (the default) the simulated providers run inside the backend process, behind an ASGI HTTP transport and in their own tables.

### Demos

- **Console:** the home page has one card per scenario (fault injection preconfigured), live pacing, and "pause at UNKNOWN". Each transaction page shows the commit barrier, the agent hierarchy, the effect DAG, *provider said vs. reality verified* per effect, authority and exposure, invariants, ground-truth external state, and the durable event timeline. Each receipt page shows the receipt with hash verification.
- **Scripts** (`scripts/`, stdlib only, `PACT_API_URL` defaults to `http://localhost:8000`): `run_all_demos.py`, `run_demo_success.py`, `run_demo_unknown.py`, `run_demo_compensation.py`, `seed_demo.py`, `run_demo_restart.py` (needs the backend venv and `PACT_DATABASE_URL`).
- **CLI:** `python -m app.cli scenario [name...]` runs scenarios in-process. `python -m app.cli crash-midflight` followed by `python -m app.cli recover` demonstrates restart recovery.

See [docs/demo.md](docs/demo.md) for a walkthrough script.

## Tests

```bash
cd backend
export PACT_TEST_DATABASE_URL=postgresql+asyncpg://pact:pact@localhost:5432/pact_test   # schema is dropped!
pytest -q
```

The suite covers unit tests of every engine (state machines, authority, conflicts, DAG, invariants, barrier, hashing, outcome classification) and integration tests against real PostgreSQL: the 12 acceptance tests, all scenarios twice each, concurrency (racing commits, racing duplicate operation keys, concurrent child budget use, optimistic version conflicts), fault injection, and restart recovery, including a real subprocess crash. Without `PACT_TEST_DATABASE_URL` only the unit tests run.

## Repository layout

```text
backend/app/domain      protocol objects + enums (Pydantic)
backend/app/core        state machine, authority, conflicts, graph, invariants, barrier,
                        transaction manager, coordinator, executor, verifier, reconciler,
                        compensator, receipts
backend/app/adapters    effect contracts + HTTP adapters (the only path to external systems)
backend/app/simulators  the simulated external world (separate app, separate state)
backend/app/agents      PactClient, scripted agents, model-provider boundary
backend/app/api         REST + SSE
backend/migrations      Alembic (incl. append-only / immutability triggers)
frontend/               Next.js operator console
docs/                   architecture, protocol, state machines, ADRs, demo, validation report
scripts/                demo scripts
```

## Model integration (NVIDIA Nemotron or any other model)

`backend/app/agents/model_provider.py` defines `PlannerProvider.propose_transaction(intent, context) -> TransactionSpec` and `ExplanationProvider.explain_decision(decision) -> str`. The default is a deterministic planner. Set `PACT_PLANNER_PROVIDER=openai_compatible` with `PACT_PLANNER_BASE_URL`/`MODEL`/`API_KEY` to use any OpenAI-compatible endpoint, such as NVIDIA NIM. `POST /api/v1/planner/propose` only returns a proposal: **model proposes → PACT validates → PACT decides.** No code path lets model output authorize a commit.

## Documentation

- [docs/architecture.md](docs/architecture.md): components, flows, consistency, trust boundaries
- [docs/protocol.md](docs/protocol.md): protocol objects, contracts, invariants, API
- [docs/state-machines.md](docs/state-machines.md): transaction, effect and logical-operation lifecycles
- [docs/adr/](docs/adr/): architecture decisions
- [docs/validation-report.md](docs/validation-report.md): scenario results and proof points

## Known limitations

See the validation report. In short: no real authentication of agents or operators (actor ids are asserted, then bound and checked server-side); effects execute sequentially; recovery runs at process start rather than through continuous workers; receipts are hashed but not signed; providers are simulated.
