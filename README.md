# PACT — Protocol for Agentic Commit Transactions

> **Phase 2.1 is implemented with local evidence; the next semantic-control phase is in progress.**
> The scenarios below describe the original simulated workflow. Current setup, evidence, and remaining gates are in the
> [Phase 2 runbook](docs/phase2/runbook.md),
> [implementation checklist](docs/phase2/implementation-checklist.md), and
> [current state report](docs/phase2/current-state-report.md).

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

### Windows PowerShell (verified locally)

Requirements: Python 3.12+, Node 20+, PostgreSQL 14+, a configured `.env`, and an existing empty `pact` development database. From the repository root:

```powershell
py -3.13 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e 'backend[dev]'
Set-Location frontend
npm.cmd ci
Set-Location ..
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_local.ps1 -SetupOperator
```

`-SetupOperator` prompts for a private password. Then open `http://localhost:3000` and sign in with tenant `local`, username `operator`. The API docs are at `http://localhost:8000/docs`. Later starts can omit `-SetupOperator`; stop the servers with `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_local.ps1`. The launcher loads `.env`, migrates the database, enables local demos, and checks both servers. It writes ignored logs under `.local/`. The existing `.env` in this checkout selects Groq and reads its key from `GROQ_API_KEY`.

After setup, `npm run dev` from the repository root starts both servers through
that launcher; `npm run stop` stops them. `npm run dev` from `frontend/` starts
only Next.js and requires the backend to be running separately.

Docker Compose is packaged but has not been verified on this host because the Docker daemon is unavailable. See the [runbook](docs/phase2/runbook.md) for manual, MCP, and worker setup.

### Demos

- **Console:** the home page has one card per scenario (fault injection preconfigured), live pacing, and "pause at UNKNOWN". Each transaction page shows the commit barrier, the agent hierarchy, the effect DAG, *provider said vs. reality verified* per effect, authority and exposure, invariants, ground-truth external state, and the durable event timeline. Each receipt page shows the receipt with hash verification.
- **Legacy scripts:** the original unauthenticated `scripts/run_demo_*.py` clients need migration to the Phase 2 API. Use the signed-in console or `python -m app.cli scenario ...` for the current simulated flows.
- **CLI:** `python -m app.cli scenario [name...]` runs scenarios in-process. `python -m app.cli crash-midflight` followed by `python -m app.cli recover` demonstrates restart recovery.

See [docs/demo.md](docs/demo.md) for a walkthrough script.

## Tests

The full suite needs a **disposable** PostgreSQL database because the fixture drops its `public` schema. Never set `PACT_TEST_DATABASE_URL` to the working `PACT_DATABASE_URL`. The test guard checks this before a reset. See the [validation report](docs/phase2/validation-report.md) for the verified command and coverage; without the test database, only unit tests run.

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

## Model integration (Groq active; NVIDIA Nemotron ready for owner key)

`backend/app/agents/model_provider.py` defines a bounded `PlanProposal` output. Select `PACT_MODEL_PROFILE=groq_dev` with `GROQ_API_KEY` for the current live profile, or `nebius_nemotron` with `NEBIUS_API_KEY` for NVIDIA Nemotron on Nebius. `POST /api/v1/planner/propose` only returns a proposal: **model proposes → PACT validates → PACT decides.** The workflow catalog is sent to the selected model only when `PACT_PLANNER_SHARE_WORKFLOW_CATALOG=true`. See the [Phase 2.1 checklist](docs/phase2_1/implementation-checklist.md) for exact setup, tests and unverified live gates.

## Documentation

- [docs/architecture.md](docs/architecture.md): components, flows, consistency, trust boundaries
- [docs/protocol.md](docs/protocol.md): protocol objects, contracts, invariants, API
- [docs/state-machines.md](docs/state-machines.md): transaction, effect and logical-operation lifecycles
- [docs/adr/](docs/adr/): architecture decisions
- [docs/validation-report.md](docs/validation-report.md): scenario results and proof points

## Known limitations

See the [next-phase release report](docs/next-phase/release-report.md), the [Phase 2.1 acceptance matrix](docs/phase2_1/implementation-checklist.md#acceptance-matrix), and the preserved [Phase 2 matrix](docs/phase2/implementation-checklist.md#acceptance-matrix). Agents and operators are authenticated, and durable workers handle recovery. Six local Edge journeys passed, including UNKNOWN reconciliation. A local Docker reference run denied the agent a direct write and completed one approved Git promotion. Business providers in that evidence are still simulated, receipts are hashed but not signed, and live Nebius/NVIDIA inference, human semantic qualification, hosted CI, and actual Code Council integration remain open.
