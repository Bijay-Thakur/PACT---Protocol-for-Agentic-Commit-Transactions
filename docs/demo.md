# Demo walkthrough (about 6 minutes)

Start: `docker compose up --build` (or the non-Docker steps in the README), then open http://localhost:3000. Set "Live pacing" to 800 ms so steps are visible.

**Setup line:** "Five autonomous agents touch billing, subscription, identity, CRM and notification. Observability can tell us what each agent did. PACT controls whether their combined consequences are allowed to become one business transaction, and proves what actually happened."

## 1. Successful transaction (`success`)
1. Run it. Show the hierarchy: root agent → five children, each with a narrowly delegated capability (Authority panel: billing may refund ≤ $200 on `customer:…/refund_balance` only).
2. Commit barrier panel: every check green, **GLOBAL COMMIT: ELIGIBLE**.
3. DAG: cancel → (CRM, revoke) → refund → notification. Each node turns green only after *verification*.
4. Click the refund: **Provider said** `ACCEPTED 201` | **Reality verified** `VERIFIED_SUCCESS`, refund found by idempotency key.
5. Receipt page: `COMMITTED_VERIFIED`, child receipt hashes, click **Verify hash**.

## 2. "A timeout is not a failure" (`unknown`, tick *Pause at UNKNOWN*)
1. The refund node turns amber: `UNKNOWN`. Provider said `RESPONSE_LOST`. The notification is not sent.
2. External reality panel: the refund **does** exist at billing. The provider call log shows exactly one `create_refund`.
3. Click **Reconcile**. PACT queries billing by operation identity; it does not re-send. The refund becomes `VERIFIED` (source: reconciliation) and the notification follows.
4. The call log still shows one `create_refund`. Key line: *"A timeout is not the same as a failure."*

## 3. Cross-agent invariant (`budget-conflict`)
1. Three billing agents, each locally `PREPARED` with a $6,000 cap: $1,800, $3,800, $5,500.
2. The barrier reports `GLOBAL_BUDGET_EXCEEDED` with exposure $11,100 against the $10,000 limit, with per-agent contributions. Zero provider calls.
3. Key line: *"The bug exists only at the transaction level, not inside any one agent."*

## 4. Partial failure (`compensation`, then `compensation-failure`)
1. The entitlement revoke is rejected. The recovery decision releases the refund and notification (never dispatched), then compensates CRM and the subscription in reverse order, each verified. Result: `COMPENSATED`.
2. `compensation-failure`: reactivation also fails → `HUMAN_REQUIRED`. The receipt is a *draft* until an operator acts. Finalize as failed: the receipt lists `subscription.cancel` under **uncompensated effects** and records the operator action.
3. Key line: *"PACT does not pretend arbitrary APIs are ACID. It makes guarantees explicit and records what actually happened."*

## Optional
- `invariant-failure`: the barrier blocks a refund that is within the agent's cap but above the unused balance. Nothing left PACT.
- `notification-ordering`: the refund stays invisible for three reads; the schedule events show the notification waiting on verification.
- `verification-mismatch`: the CRM said 200 but didn't apply the change; verification fails, and PACT compensates.
- `duplicate-operation`: the same refund `operation_key` from another run → `OPERATION_ALREADY_VERIFIED`.
- Restart: `PACT_DATABASE_URL=... python scripts/run_demo_restart.py` kills the process right after the refund is applied, and a fresh process completes it with no duplicate.
