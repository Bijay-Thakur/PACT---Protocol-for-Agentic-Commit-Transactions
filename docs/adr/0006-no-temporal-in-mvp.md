# ADR-0006: No workflow engine in the MVP

**Status:** accepted

**Decision.** The coordinator is a plain async service whose entire progress state is in PostgreSQL. `Coordinator.recover_root(root_id)` resumes any in-flight transaction from the database. It runs at startup and via `python -m app.cli recover`.

**Why.** PACT's semantics (the barrier, authority, UNKNOWN handling) must stay visible and owned by PACT, rather than looking like "Temporal + agents".

**Consequences.** A durable execution engine can later host `drive`/`recover_root` as a workflow, with adapter calls as activities, without changing protocol objects, state machines or receipts. Today recovery happens on process start rather than through continuous workers: if the process is down, in-flight transactions wait safely until it restarts.
