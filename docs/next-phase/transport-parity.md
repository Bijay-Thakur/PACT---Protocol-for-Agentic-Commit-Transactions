# REST, MCP, and convenience-client parity

- REST is the complete `/api/v1` lifecycle and the generated contract source.
- MCP exposes agent-safe discovery, begin/delegate/propose, prepare, exact-digest
  commit, status, and repair operations through the same service boundary.
- Approval, credential management, and residual attestation remain
  operator-only REST/UI operations by design.
- `HttpPactClient.commit` and `PactClient.commit` both require the reviewed
  digest. `reconcile` is present in both and maps to the authorized operator
  action route; agent credentials without that scope receive a typed denial.
- Request replay IDs, business-operation identities, and provider idempotency
  keys are separate at every transport.

The independent Python and Node MCP checks are historical Phase 2.1 evidence
until rerun against migration 0004. The raw-HTTP fixture is the external
conformance client for this phase.
