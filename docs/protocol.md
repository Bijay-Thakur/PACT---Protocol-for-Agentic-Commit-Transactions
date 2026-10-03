# PACT protocol objects (v0.1)

All objects are Pydantic models in `backend/app/domain/`. The OpenAPI schema is at `GET /openapi.json` (interactive docs: `/docs`).

## TransactionSpec (root)

```json
{
  "objective": "Cancel customer C-48291, refund the unused subscription period ...",
  "actor_id": "root_agent",
  "capability": {
    "issuer": "operator:demo",
    "allowed_effect_types": ["billing.refund", "crm.update", "identity.revoke", "notification.send", "subscription.cancel"],
    "allowed_resources": ["customer:C-48291/*"],
    "amount_limit": "500.00",
    "cumulative_amount_limit": "500.00",
    "delegation_depth": 2,
    "expires_at": null
  },
  "invariants": [ { "key": "root_budget", "phase": "PRE_COMMIT", "expression_type": "sum_amount_lte",
                    "config": {"limit_ref": "root.capability.cumulative_amount_limit"},
                    "failure_action": "BLOCK_COMMIT", "name": "Sum of child spend <= root authority" } ],
  "required_effect_types": ["billing.refund", "..."],
  "requires_approval": false,
  "recovery_policy": {"on_effect_failure": "COMPENSATE", "on_unknown": "RECONCILE", "auto_reconcile": true},
  "children": [ /* ChildSpec, optional - may also be added incrementally */ ],
  "effects": [],
  "metadata": {"customer_id": "C-48291"}
}
```

The root `issuer` must be configured in `PACT_ISSUER_POLICIES`; amounts must be bounded by that issuer's maximum.

## ChildSpec (delegation) — `POST /transactions/{id}/children`

```json
{ "actor_id": "billing_agent", "objective": "Refund the unused period",
  "capability": { "allowed_effect_types": ["billing.refund"], "allowed_resources": ["customer:C-48291/refund_balance"],
                  "amount_limit": "200.00", "cumulative_amount_limit": "200.00", "delegation_depth": 0 },
  "required": true }
```

Rejected (`403 DELEGATION_REJECTED`, details list) when it adds effect types, broadens resources, raises/unbounds amounts, exceeds delegation depth, outlives the parent, or the parent is expired. Resource patterns support exact ids and one trailing `*`.

## EffectProposal — `POST /transactions/{id}/effects`

```json
{ "effect_type": "billing.refund", "actor_id": "billing_agent",
  "operation_key": "customer:C-48291/refund:unused-period:2026-10-02",
  "payload": {"customer_id": "C-48291", "amount": "143.27"},
  "resource_claims": [{"resource": "customer:C-48291/refund_balance", "mode": "EXCLUSIVE"}],
  "depends_on": ["customer:C-48291/subscription:cancel:2026-10-02"] }
```

`actor_id` must equal the transaction's actor. The payload is validated against the contract's schema. Contract-required claims are merged in (an agent cannot under-declare). `depends_on` references operation keys anywhere in the same root tree. Proposing never executes anything.

## EffectContract — `GET /contracts`

`effect_type, adapter_name, resource_type, operation_kind, required_capability, reversibility_class (REVERSIBLE | COMPENSABLE | IRREVERSIBLE | ...), prepare_strategy, commit_strategy, verification_strategy, compensation_strategy, idempotency_strategy, timeout_seconds, max_attempts, verification_polls, reconciliation_policy (QUERY_BY_IDEMPOTENCY_KEY | QUERY_TARGET_STATE | MANUAL), evidence_requirements, amount_field, contradicts`.

| effect | reversibility | compensation | reconciliation |
|---|---|---|---|
| `billing.refund` | IRREVERSIBLE | none | by idempotency key |
| `subscription.cancel` | COMPENSABLE | reactivate | target state |
| `identity.revoke` / `identity.grant` | COMPENSABLE | inverse | target state |
| `crm.update` | REVERSIBLE | restore before-image | target state |
| `notification.send` | IRREVERSIBLE | none | by idempotency key |

## Invariant expression types

| type | config | meaning |
|---|---|---|
| `effect_amount_lte` | `effect_type`, `limit` and/or `limit_refs[]` | each effect amount ≤ min of limits |
| `sum_amount_lte` | `effect_type?`, `limit` or `limit_ref` | sum across the subtree ≤ limit |
| `field_matches` | `field`, `equals_ref` | every payload with `field` equals the reference |
| `unique_operation_keys` | – | no logical operation twice |
| `depends_on_verified` | `effect_type`, `requires[]` | pre-commit: DAG dependency exists; post: dispatched after prerequisites verified |
| `implies_verified` | `if_effect_type`, `then_effect_type` | if A verified then B verified |
| `single_external_match` | `effect_type` | verification evidence shows exactly one external record |

References: `capability.amount_limit`, `root.capability.amount_limit|cumulative_amount_limit`, `observation:<effect_type>.<key>`, `metadata.<key>`. Unresolvable references and evaluator errors **fail closed**. `failure_action`: `BLOCK_PREPARE | BLOCK_COMMIT | ABORT | RECONCILE | COMPENSATE | HUMAN_REQUIRED`.

## CommitDecision — `GET /transactions/{id}/commit-decision` (dry run) / in commit response (binding)

```json
{ "eligible": false, "binding": true, "snapshot_version": 9,
  "checks": [ {"code": "GLOBAL_BUDGET", "passed": false, "subject": "root_agent",
               "observed": {"exposure": "11100.00", "limit": "10000.00", "contributions": [...]},
               "blocking_reason": "GLOBAL_BUDGET_EXCEEDED"} ],
  "blocking_reasons": ["GLOBAL_BUDGET_EXCEEDED", "INVARIANT_FAILED:root_budget"] }
```

Check codes: `TRANSACTION_STATE_VALID, CHILD_PREPARED, REQUIRED_EFFECTS_PRESENT, EFFECT_CONTRACTS_RESOLVED, DEPENDENCY_GRAPH_VALID, AUTHORITY_VALID, NO_AUTHORITY_ESCALATION, GLOBAL_BUDGET, CUMULATIVE_AUTHORITY, RESOURCE_CLAIMS_COMPATIBLE, INVARIANT, IDEMPOTENCY, APPROVALS_SATISFIED, NO_BLOCKING_UNKNOWN`.

## Results across the adapter boundary

- `DispatchResult.outcome`: `ACCEPTED | REJECTED_DEFINITIVE | REJECTED_RETRYABLE | RESPONSE_LOST | NOT_SENT`
- `VerificationResult.status`: `VERIFIED_SUCCESS | VERIFIED_FAILURE | VERIFICATION_PENDING | VERIFICATION_UNKNOWN` + `evidence`
- `ReconciliationFinding.finding`: `APPLIED | NOT_APPLIED | INDETERMINATE | CONFLICTING` → outcome `VERIFIED_SUCCESS | SAFE_TO_RETRY | STILL_UNKNOWN | HUMAN_REQUIRED`

## TransactionReceipt — `GET /transactions/{id}/receipt`, `/receipt/verify`

Fields: `receipt_version ("pact.receipt/v1"), transaction_id, root_id, parent_id, objective, initiator, participants, created_at, finalized_at, final_state, outcome_statement, capability_summary, children (with receipt_hash), effects, invariant_results, commit_decision, resource_claims, execution_results, verification_results, reconciliation_events, compensation_results, uncompensated_effects, human_actions, external_references, event_count, receipt_hash`.

Canonicalization: sorted keys at every depth; compact separators; UTF-8; decimals as normalized fixed-point strings; datetimes ISO-8601 UTC (`Z`); UUIDs/enums as strings; `receipt_hash` excluded from the hash input; SHA-256 hex. Non-terminal transactions return `409 RECEIPT_NOT_FINAL` with an unhashed `draft`.

## Errors

Always `{"error": {"code": "...", "message": "...", "details": ...}}`. Examples: `DELEGATION_REJECTED`, `ROOT_GRANT_REJECTED`, `ACTOR_NOT_BOUND_TO_TRANSACTION`, `EFFECT_PAYLOAD_INVALID`, `UNKNOWN_EFFECT_TYPE`, `SPECIFICATION_CLOSED`, `CHILD_CANNOT_COMMIT_INDEPENDENTLY`, `COMMIT_ALREADY_IN_PROGRESS`, `NOTHING_TO_RECONCILE`, `COMPENSATION_NOT_PERMITTED`, `CONCURRENCY_CONFLICT`, `INVALID_STATE_TRANSITION`, `RECEIPT_NOT_FINAL`.

## API surface

```
POST /api/v1/transactions                       GET /api/v1/transactions
GET  /api/v1/transactions/{id}                  POST /api/v1/transactions/{id}/children
POST /api/v1/transactions/{id}/effects          POST /api/v1/transactions/{id}/invariants
POST /api/v1/transactions/{id}/prepare          POST /api/v1/transactions/{id}/commit
POST /api/v1/transactions/{id}/reconcile        POST /api/v1/transactions/{id}/compensate
POST /api/v1/transactions/{id}/operator-actions GET  /api/v1/transactions/{id}/commit-decision
GET  /api/v1/transactions/{id}/events           GET  /api/v1/transactions/{id}/events/stream  (SSE)
GET  /api/v1/transactions/{id}/receipt          GET  /api/v1/transactions/{id}/receipt/verify
GET  /api/v1/contracts
GET  /api/v1/demo/scenarios                     POST /api/v1/demo/run/{scenario}
GET  /api/v1/demo/external-state/{customer_id}
POST /api/v1/planner/propose                    GET  /api/v1/planner/explain/{id}
```
