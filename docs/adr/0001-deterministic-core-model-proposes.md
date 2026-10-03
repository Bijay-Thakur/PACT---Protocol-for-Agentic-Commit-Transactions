# ADR-0001: Deterministic core; models only propose

**Status:** accepted

**Context.** Commit decisions over money, access and customer state must be reproducible and explainable. LLM output is neither.

**Decision.** Every commit-critical computation is deterministic code over a snapshot: state transitions, capability subset checks, budget arithmetic (`Decimal`/`NUMERIC`), conflicts, invariants, DAG ordering, the barrier, verification handling, reconciliation, compensation ordering, and receipt hashing. Model integration exists only as `PlannerProvider` (returns a `TransactionSpec` *proposal*) and `ExplanationProvider` (advisory text) in `app/agents/model_provider.py`. The planner endpoint returns a proposal; it never creates or commits anything. Invariant evaluators fail closed on missing data or errors.

**Consequences.** The MVP runs with no model and no API key. A Nemotron/NIM planner plugs in via `PACT_PLANNER_PROVIDER=openai_compatible`. Its output still has to pass schema validation, issuer policy, delegation checks, invariants and the barrier. This is tested: an inflated planner spec is rejected with `ROOT_GRANT_REJECTED`.
