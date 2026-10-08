# Reproducible walkthroughs

## Intent to verified receipt

Prerequisites: migrated disposable PostgreSQL, simulators, worker, synthetic
customer, requester API key, and a distinct approver API key.

1. Seed the synthetic customer in the simulator.
2. Set `PACT_BASE_URL`, `PACT_REQUESTER_API_KEY`,
   `PACT_APPROVER_API_KEY`, and `PACT_SYNTHETIC_CUSTOMER_ID`.
3. Run `python examples/raw-http/lifecycle.py`.
4. Inspect the printed transaction, final digest, receipt hash, and local receipt
   verification result.
5. Independently inspect simulator calls/state. Exactly one business refund
   effect is expected; HTTP attempt count may be greater only where justified by
   the pinned contract.

The fixture imports no PACT service internals and has no database or provider
credential. It is a future-SDK conformance proof, not an SDK.

## Semantic or external uncertainty

Semantic hold:

1. Submit `Cancel C-EV16 after the billing period`.
2. Review the trace against a proposal reduced to immediate cancellation.
3. Observe `TIMING_CONSTRAINT_OMITTED` and `NEEDS_CLARIFICATION`; no effect is
   prepared or dispatched.
4. Supply an attributable, issue-specific clarification. Material edits create a
   new candidate/final digest and require fresh approval.

External uncertainty:

1. Run the existing lost-response scenario against disposable simulators.
2. Observe durable dispatch intent followed by `UNKNOWN`.
3. Verify no blind second send occurs.
4. Reconcile by operation identity and independent provider observation.
5. If evidence proves application, continue under the pinned plan; if evidence
   remains insufficient or restoration fails, retain `HUMAN_REQUIRED` and an
   explicit residual obligation in the receipt.
