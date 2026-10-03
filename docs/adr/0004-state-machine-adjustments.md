# ADR-0004: Small, documented state-machine adjustments

**Status:** accepted

The plan's state machines are implemented as specified, with these additions:

1. **`RECONCILING → COMMITTING`.** After ambiguity resolves (for example, the refund is found and verified), the rest of the DAG still has to execute, including the confirmation notification. The plan's `RECONCILING → VERIFYING` is kept; this edge lets execution continue.
2. **Effect state `ABORTED`.** An effect that was never dispatched (the barrier rejected it, or recovery released it) is neither `FAILED` nor `COMPENSATED`. Recording it as `ABORTED` keeps receipts truthful ("never dispatched").
3. **Children mirror the root after the barrier.** Children cannot commit independently. Once the root crosses the barrier, the tree moves through the global phases together. Each child's local validity is preserved in its own `PREPARED` transition event and in the barrier's `CHILD_PREPARED` checks.
4. **Irreversible residue escalates.** `COMPENSATED` is entered only when every committed effect has been restituted and verified. If an irreversible effect remains, or a compensation fails, the transaction goes to `HUMAN_REQUIRED`. An operator can then retry, attest manual restitution, or finalize as `FAILED_TERMINAL`; the receipt lists `uncompensated_effects`.
5. **A prepare failure aborts only that child.** The root still evaluates the barrier, which reports `CHILD_NOT_PREPARED:<actor>`, so the barrier remains the single decision point.

None of these change the product thesis. They make "truthful final state" precise.
