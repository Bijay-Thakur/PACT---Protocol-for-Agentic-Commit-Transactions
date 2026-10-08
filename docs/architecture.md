# PACT architecture (as built)

PACT is a transaction and commit layer. Agents propose; PACT decides, executes, verifies, recovers, and issues receipts.

```text
 agents (scripted today; LangGraph / CrewAI / MCP / model-backed later)
   │  PactClient: create tx, delegate, propose, prepare, request commit   (REST only)
   ▼
┌──────────────────────────── PACT backend (FastAPI) ────────────────────────────┐
│ api/            thin routes → domain services, structured error codes          │
│ core/transaction_manager  creation, delegation, proposals, ALL state changes   │
│ core/authority_engine     issuer policy, subset delegation, chain, exposure    │
│ core/conflict_manager     READ/WRITE/EXCLUSIVE, contradictions, duplicates     │
│ core/effect_graph         DAG, cycles, deterministic topo order, readiness     │
│ core/invariant_engine     code-backed declarative invariants (fail closed)     │
│ core/commit_barrier       pure function: snapshot → CommitDecision             │
│ core/coordinator          prepare → barrier → ordered execution → verify →     │
│                           reconcile / compensate / escalate → finalize         │
│ core/executor             intent persisted BEFORE dispatch, outcome classified │
│ core/verifier             independent external reads, bounded polling          │
│ core/reconciler           UNKNOWN → query by operation identity                │
│ core/compensator          reverse order, verified, honest about residue        │
│ core/receipt_generator    canonical JSON, SHA-256, child-hash chaining         │
│ adapters/                 EffectContract + adapter per effect type (HTTPX)     │
│ agents/model_provider     PlannerProvider / ExplanationProvider (advisory)     │
│ telemetry/                OpenTelemetry spans with pact.* attributes           │
└───────────────┬──────────────────────────────────────────────┬─────────────────┘
                │ SQLAlchemy (asyncpg)                         │ HTTP only
                ▼                                              ▼
        PostgreSQL (source of truth)                 Simulated providers (own DB)
        transactions, effects, capabilities,         billing · subscription ·
        invariants, evaluations, logical_ops,        identity · CRM · notification
        attempts, events (append-only),              + fault injection + call log
        commit_decisions, receipts (immutable)
```

## Key flows

**Prepare** (`Coordinator.prepare`): children first, each independently. Per child: close specification, check each effect against the child's *own* capability (local authority), run the adapter's read-only `prepare` against external state (e.g. subscription active, unused balance 143.27, CRM before-image), evaluate PREPARE-phase invariants. Failure → child `ABORTED`.

**Commit** (`Coordinator.commit`, root only): in one PostgreSQL transaction, lock the root row (aggregate lock), all tree rows and the tree's logical-operation rows (ordered by key), build a `TreeSnapshot`, evaluate the barrier, persist the `CommitDecision` + invariant evaluations + event. Eligible → reserve logical operations for this root, persist DAG edges, move tree to `COMMITTING`. Ineligible → abort tree, receipts. Concurrent commits serialize on the root lock; the loser sees `COMMITTING` and gets `COMMIT_ALREADY_IN_PROGRESS`.

**Execution** (`Coordinator.drive`): repeatedly lock, snapshot, pick the next effect in deterministic topological order whose dependencies are all **VERIFIED**, dispatch, verify. An `UNKNOWN` halts execution (no blind retry). A `FAILED` effect triggers the recovery decision. No locks are held during external calls.

**Recovery decision**: evaluate POST_EXECUTION invariants + recovery policy → release never-dispatched effects (`ABORTED`, logical ops freed) → compensate or escalate.

**Final verification**: re-read every verified effect from the external systems, evaluate POST_EXECUTION + FINAL invariants (e.g. exactly one external refund per operation key), then `COMMITTED_VERIFIED` or recovery.

**Restart recovery** (`Coordinator.resume_inflight`, run at startup): finds roots in `COMMITTING/VERIFYING/UNKNOWN/RECONCILING/COMPENSATING`; effects caught mid-flight (`DISPATCHING/DISPATCHED/VERIFYING/RECONCILING`) become `UNKNOWN` with their attempt marked `ABANDONED_BY_RESTART`; then reconcile / continue / compensate purely from database state.

## Consistency

- Aggregate lock: every mutating unit of work does `SELECT … FOR UPDATE` on the root first.
- Optimistic concurrency: `version` columns (`version_id_col`) on transactions, effects, logical operations; a stale write raises `CONCURRENCY_CONFLICT`.
- `UNIQUE(operation_key)` arbitrates proposal races (`INSERT … ON CONFLICT DO NOTHING`).
- State + event are written atomically. Events are append-only and receipts immutable via triggers.
- Budget arithmetic uses `NUMERIC(18,2)` columns read under the lock — never process memory.

## Trust boundaries

1. Agents are untrusted: they only hold a REST client; actor ids are bound to transactions; capabilities are derived server-side by delegation; root authority needs a configured issuer (`PACT_ISSUER_POLICIES`).
2. Adapters are the external-effect boundary: they get immutable `EffectView`s, never sessions.
3. Provider responses are not truth: verification re-reads state.
4. Operator actions are recorded (`operator_actions` + events) and appear on receipts.
5. Receipts are immutable after finalization (no mutation routes, DB trigger).

Implemented authentication uses server-issued API keys for agents and operators; actor and tenant identity are derived from the authenticated principal and checked against server-side grants. A local Docker reference run measured denied direct writes and one PACT-mediated Git promotion; that is not a production deployment certification. Enterprise identity federation, signed receipts, and production-provider adapters are not implemented. Tenant scoping exists in the service and persistence model, but has not received a separate production multi-tenancy certification.

## Simulated external world

`app/simulators` is a separate FastAPI app with its own SQLAlchemy metadata and (optionally) its own database. In development it is reached through an in-process ASGI transport; in `docker compose` it runs as a separate `simulators` service with database `pact_sim`. Either way, PACT reaches it only over HTTP through adapters. Fault injection is per system/operation/customer and consumed on use. The provider-side call log is what proves "exactly one refund call".

## Where Temporal / LangSmith / MCP fit

- **Temporal**: the `drive` loop is resumable from Postgres alone (`recover_root`), so a durable-execution engine could host it as a workflow without changing protocol semantics (ADR-0006).
- **OpenTelemetry / LangSmith / Phoenix**: spans `pact.transaction.*`, `pact.commit_barrier.evaluate`, `pact.effect.dispatch|verify|reconcile|compensate`, `pact.receipt.finalize` with `pact.transaction_id`, `pact.root_id`, `pact.effect_id`, `pact.operation_key`, `pact.actor_id`, `pact.state`, `pact.effect_type`. Set `PACT_OTEL_EXPORTER=console|otlp`.
- **MCP**: an MCP tool becomes an effect by writing an adapter + contract; the MCP server is unchanged.
