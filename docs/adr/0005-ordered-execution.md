# ADR-0005: Sequential, deterministic DAG execution gated on verification

**Status:** accepted

**Decision.** After the barrier, effects execute one at a time in topological order, with ties broken by `operation_key`. An effect becomes ready only when every dependency is **VERIFIED**, not merely dispatched. Any `UNKNOWN` halts progression until reconciliation. In the demo DAG, irreversible effects run last: the refund depends on the cancellation *and* the entitlement revocation, and the notification depends on all four business effects.

**Why.** Determinism makes runs reproducible and receipts comparable. Gating on verification prevents "confirmation sent before the refund is real" (Scenario F). Running irreversible effects last minimizes exposure that cannot be restituted when something earlier fails: Scenario E ends `COMPENSATED` with the refund never dispatched.

**Consequences.** There is no parallelism within a level; the latency cost is acceptable for the MVP. `EXECUTION_SCHEDULE_EVALUATED` events record which effects were waiting on which unverified prerequisites. Parallel dispatch within a level is a straightforward future change, since readiness is already computed per effect.
