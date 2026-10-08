# PACT service contract v1

The executable schema is `openapi-v1.json`, exported from `create_app().openapi()`.
The stable transport prefix is `/api/v1`. This document describes lifecycle
semantics that OpenAPI types alone cannot express.

## Lifecycle

1. Discover workflows/contracts using authenticated discovery.
2. Propose natural-language intent. The response is advisory and `applied=false`.
3. Review with the immutable `proposal_trace_id`; a substituted proposal fails.
4. Explicitly accept attributable clarifications using a unique replay ID.
5. Propose or invoke a registered trusted assembler, then prepare.
6. Preparation reads facts, compiles consequences, performs semantic comparison,
   and freezes a revision. `candidate_digest` precedes semantic assessment; the
   final digest binds candidate, assessment, policy, contracts, facts, graph,
   projection, and accepted semantics without a digest cycle.
7. An authorized, distinct operator approves the exact final digest.
8. Commit requires that same digest. `QUEUED` means durable dispatch work exists;
   it does not mean provider state is verified.
9. Poll status/events. `UNKNOWN` is nonterminal and prohibits blind replay.
10. Reconcile or perform server-permitted incident actions. Only independent
    observation supports `COMMITTED_VERIFIED`.
11. Read and locally verify the immutable receipt and amendment chain.

## Identities and replay

- `X-PACT-Request-ID` deduplicates one authenticated HTTP mutation. Reusing it
  with another body, route, target, principal, or tenant conflicts.
- Business-operation identity is server-derived from tenant, provider, business
  request, slot, resource, and epoch. A new HTTP ID cannot duplicate it.
- Provider idempotency keys are derived by the adapter from the frozen operation
  identity and are not caller-selected.
- Expected draft version and exact revision digest protect concurrent edits.

Errors have `{error: {code, message, details}}`. HTTP status distinguishes
authentication, authorization, validation, conflict, and missing resources;
callers should branch on stable `code`, not message text.

Approval is intentionally absent from agent convenience interfaces where an
operator separation is required. REST and MCP share lifecycle services but do
not promise identical privilege exposure. No SDK is produced by this phase.
