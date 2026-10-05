# PACT Phase 2 — implementation checklist and progress log

Source of truth: `PACT_Phase_2_Build_Plan_and_Master_Prompt.md` (repo root). Status values: PASS / PARTIAL / FAIL / BLOCKED / NOT_RUN / IN_PROGRESS.

## Environment facts (P2-00, 2026-10-03)

| Item | Finding |
|---|---|
| Checkout | git repo, `main`, 2 commits (`b3672ec` first commit, `0b4bc3f` logo + neumorphism UI). Clean except the Phase 2 plan file. Remote `origin` = GitHub. **No push/commit by the agent.** |
| Baseline backend | `pytest -q` with `PACT_TEST_DATABASE_URL=…localhost:55432/pact_test` → **110 passed** (31 s). Without DB: 70 passed, 40 skipped. |
| Baseline frontend | `npm run lint` clean, `tsc --noEmit` clean, `npm run build` (Next 16.3.8, Turbopack) **succeeds** in this environment. |
| Databases | Owner's working DB: PostgreSQL 16 service on `localhost:5432` (from `.env`). Disposable test DB: portable PostgreSQL 18.4 on `localhost:55432` (agent scratchpad), databases `pact_test`, `pact_sim`, `pact_compose`. Destructive fixtures are only ever pointed at 55432. |
| Docker | Installed but daemon cannot start (no WSL). Compose remains untested packaging. |
| Nebius / NVIDIA | `.env` has `PACT_PLANNER_PROVIDER` set but base URL, model and API key **empty** → live inference (A39) BLOCKED until the owner adds a key. |
| Code Council / RivalScope | Source **not present** on this machine (searched D:\ and the user profile). → harness only; REAL_PROJECT_NOT_RUN. |
| Review characterization tests | The 8 diagnostic tests from the architecture review are not in the repo; each finding is reproduced here as a regression test before fixing. |
| Tooling | git 2.55, Node 24, Python 3.13 venv, `mcp` 2.3.0 (PyPI), pinned `@modelcontextprotocol/client` 2.3.0 in the Node example. |

## Key Phase 2 design decisions (fit to the existing code)

1. **Identity**: bearer API keys → `principals` (tenant-scoped; SHA-256 verifier of a 256-bit random key; revocable). Operators log in for a session cookie (HttpOnly, SameSite=Strict) + CSRF header. Request-body `actor_id` / `issuer` / `tenant_id` / `operator_id` are never trusted; a mismatching value is rejected as forged.
2. **Workflows / policy packs are server configuration** (`app/policy/`). Root authority comes from the principal's workflow grant, not from the request. Mandatory effects, dependencies, invariants, refund eligibility and approval thresholds are compiled in by trusted code. Caller-supplied invariants are no longer accepted.
3. **Plan revisions**: prepare = close draft → compile → read-only preparation → freeze revision with a canonical digest (covers every effect's resolved payload, ids, claims, dependencies, authority envelope, policy/contract versions, provider preconditions). Approvals bind the digest. `revise` opens a new revision; old approvals no longer match.
4. **Outcome truth**: effects carry `application` (NOT_SENT / NOT_APPLIED_CONFIRMED / APPLIED / UNKNOWN), `postcondition` (MATCH / MISMATCH / PARTIAL / UNDETERMINED), `restoration` (NOT_REQUIRED / PENDING / RESTORED / PARTIAL / RESIDUAL / UNKNOWN / HUMAN_ATTESTED). Observations and residual obligations are persisted rows.
5. **Reservations**: materialized canonical resource rows (hierarchical keys) + reservation rows + durable budget ledger (holds/consumption), all taken in the single binding barrier transaction with stable lock order.
6. **Business operation identity** derived server-side (tenant / provider / workflow business request / action slot / canonical resource / epoch) + immutable payload fingerprint. Caller `operation_key` is a hint only.
7. **Durable worker**: `work_items` Postgres queue (claim with `FOR UPDATE SKIP LOCKED`, lease, heartbeat, epoch fencing, backoff, deadline). Embedded worker in the API process (configurable) + standalone `python -m app.worker`. Sweeper repairs stranded work. Startup no longer assumes all in-flight work is dead.
8. **MCP**: official Python SDK, stdio server, thin authenticated client of the REST API (one API key per agent process). Independent client tests via the SDK client in a separate process + a Node example.
9. **Nemotron**: restricted `PlanProposal` schema → trusted compiler. Explicit model mode never falls back silently; deterministic fixture is labelled.
10. **Code Council**: not available → independently runnable harness + git sandbox adapter (real disposable repo, CAS ref promotion). Arbitrary test execution is UNSUPPORTED (no isolation boundary on this host); fixture checks only.

## Packages (2026-10-03)

| Package | Status | Evidence and next gap |
|---|---|---|
| P2-00 Baseline and guardrails | PASS | Baseline above; `tests/db_guard.py` and `test_unit_db_guard.py`; destructive fixtures used only on disposable 55432 DB. |
| P2-01 Outcome truth | IN_PROGRESS | Applied wrong amount/currency, duplicate refund, wrong recipient and partial CRM update retain adverse truth and residuals; all 9 demos pass. More restoration and drift cases remain. |
| P2-02 Trusted plan boundary | IN_PROGRESS | Auth, policy packs, revisions, approvals and restricted model review wired. More forged-identity and stale-approval cases remain. |
| P2-03 Atomic ownership | IN_PROGRESS | Real Postgres overlapping-resource barrier and aggregate/subresource READ/WRITE matrix pass; longer hold lifecycle remains. |
| P2-04 Durable worker | IN_PROGRESS | Embedded/standalone worker, sweeper, manual process crash/recovery and two-worker lease/fencing tests verified. Other crash windows remain. |
| P2-05 Contract compliance | IN_PROGRESS | Versioned contracts, observation ledger and adverse provider tests pass, including delayed empty reads and apply-then-503. Wider contract matrix remains. |
| P2-06 MCP and clients | IN_PROGRESS | SDK stdio protocol passes independent Python and pinned Node clients; malformed arguments, identity isolation and disconnect/queued replay tested. |
| P2-07 Nemotron and review flow | IN_PROGRESS | Restricted proposal, sanitized usage, 50-intent fixture, console intent panel, CLI smoke. Live A39 is BLOCKED by absent owner credentials; model-to-executable draft automation remains incomplete. |
| P2-08 First external integration | IN_PROGRESS | Optional PACT Git sandbox workflow and real disposable ref promotion pass; Code Council source absent, no isolated arbitrary test execution, no actual project hook. HARNESS_VERIFIED / REAL_PROJECT_NOT_RUN. |
| P2-09 Release evidence | IN_PROGRESS | Populated Phase 1 migration passes, frontend lint/type/build pass, docs added, and the current backend suite is green with an explicit Phase 1 test transition. Browser walkthrough and Docker Compose remain. |

## Acceptance matrix

`PASS` means the named acceptance behavior has direct evidence. `NOT_RUN` includes cases with implementation but no adequate acceptance test. See [validation-report.md](validation-report.md) for commands and limits.

| ID | Status | Evidence or outstanding check |
|---|---|---|
| A01 | PASS | `test_required_offboarding_actions_cannot_be_omitted` rejects an incomplete workflow. |
| A02 | PASS | Forged actor/issuer/tenant/operator assertions are rejected in auth/policy tests; root grants are server-owned. |
| A03 | PASS | `test_delegation_cannot_expand_resource_amount_or_depth` also checks expiry expansion. |
| A04 | PASS | Budget-conflict demo checks zero provider mutations before barrier. |
| A05 | PASS | Persisted frozen-plan digest changes for effect removal, payload, dependency, resource, policy version, contract version and authority edits; approval binds that digest and a revised amount needs new approval. |
| A06 | PASS | Policy test checks wrong role/digest, changed revision and expired approval. |
| A07 | PASS | `test_applied_wrong_refund_retains_residual`. |
| A08 | PASS | Adverse provider tests cover wrong currency, duplicate refund, wrong recipient and partial CRM update; each records application mismatch/residual. |
| A09 | PASS | Lost-response test sees a conservative refund budget hold while the effect is UNKNOWN. |
| A10 | PASS | Delayed empty lookup outlasts polling with declared lag; result remains UNKNOWN and negative evidence non-authoritative. |
| A11 | PASS | Apply-then-503 results in one provider refund call and verified reconciliation. |
| A12 | PASS | `retry_justification` refuses expired dedup without authoritative negative evidence. |
| A13 | PASS | Renamed-key replay with the same payload is rejected as already satisfied; changed payload conflicts with immutable fingerprint. |
| A14 | PASS | Renamed caller key cannot repeat the business action; authorized new epoch freezes a distinct identity. |
| A15 | PASS | `test_overlapping_refund_resources_serialize_different_business_operations`. |
| A16 | PASS | Real Postgres aggregate/subresource READ/READ compatibility and READ/WRITE/EXCLUSIVE conflicts. |
| A17 | PASS | Same concurrent barrier test creates previously unseen resource. |
| A18 | PASS | UNKNOWN refund hold survives a worker lease takeover and terminal failed closure with retained reservation; an observed refund converts hold to consumed spend. |
| A19 | PASS | `test_live_workers_do_not_steal_and_stale_epoch_cannot_finish` uses concurrent real Postgres claims. |
| A20 | PASS | Before intent, after intent/before provider, after provider application/before result persistence, and before receipt finalization recover without duplicate provider calls or a receipt gap. |
| A21 | PASS | `test_late_provider_response_is_evidence_only_then_new_worker_reconciles` records non-authoritative late evidence, then verifies one refund. |
| A22 | PASS | Stalled child PREPARING discards a late read; worker death after RECONCILING lookup and after COMPENSATING inverse call each recovers without another protected mutation. |
| A23 | PASS | Lost response after subscription restoration leaves one inverse provider call and observed RESTORED state. |
| A24 | PASS | Pre-existing absent premium stays absent during compensation with no grant call; grant-already-present is observed as a provider no-op, with no revoke. |
| A25 | PASS | External edit after prepare blocks commit; external edit after cancellation blocks stale restoration and retains the newer plan. |
| A26 | PASS | Resolved charge is pinned at prepare; wrong customer, charge, amount, currency and status each yield applied mismatch with a residual. |
| A27 | PASS | Wrong recipient, wrong template/content and duplicate delivery all yield applied mismatch and residuals. |
| A28 | PASS | Invariant reference requires exactly one source for the same customer, even if duplicate sources have equal values; missing or different target fails closed. |
| A29 | PASS | Final observation IDs and evidence digests match the immutable receipt's final set. |
| A30 | PASS | Post-finalization operator attestation says human, not provider-verified; original mismatch remains in the receipt. |
| A31 | PASS | Adverse receipt hash/payload stay unchanged after a linked amendment; populated Phase 1 v1 receipts remain verifiable. |
| A32 | PARTIAL | REST/MCP/SDK share the authenticated application services. A new cookie-session/CSRF integration test confirms the console's approval endpoint sees the same frozen digest, rejects wrong digests and agent approval, and lets the initiating agent queue the approved plan. Browser interaction remains unverified. |
| A33 | PASS | `test_stdio_mcp_calls_authenticated_rest_from_separate_process`, including Node, malformed arguments, isolation. |
| A34 | PASS | MCP client disconnects after QUEUED, reconnects and replays commit; durable worker verifies one provider refund call. |
| A35 | PASS | Auth test checks transaction, events, receipt, verification and decision denial. |
| A36 | PASS | Agent approval is rejected; MCP has no approval tool or raw provider credential argument. |
| A37 | PASS | Privileged fields and injection are rejected or treated as data; a proposal reviewed by an agent without a workflow grant remains unapplied and unauthorized. |
| A38 | PASS | `test_model_outage_never_falls_back_to_fixture` and labeled fixture review. |
| A39 | BLOCKED | No configured Nebius/NVIDIA key, model and endpoint supplied by owner. |
| A40 | BLOCKED | Code Council source unavailable; PACT Git harness verified, actual hook not run. |
| A41 | PASS | Git harness tree/evidence binding, stale base, unsafe path, conflict; PACT approval/worker path. |
| A42 | PASS | Git harness atomic `update-ref` race and lost-response ref observation. |
| A43 | NOT_RUN | Separate enforcement deployment and agent filesystem isolation needed. |
| A44 | PASS | `test_int_migration_upgrade.py` on populated disposable Phase 1 DB. |
| A45 | PASS | `test_unit_db_guard.py`; working DB is excluded. |
| A46 | NOT_RUN | Production build passes; browser end-to-end intent/approval/incident flow could not run with available browser automation in this environment. |
| A47 | PASS | `test_int_phase2_demo.py` runs all nine simulated scenarios. |
| A48 | PASS | Ten valid simulated requests (six varied remediation amounts/profiles and four offboardings) froze, queued and verified; measured false blocks 0/10. |

## Progress log

- 2026-10-03: P2-00 inspection and Phase 1 baseline recorded by Claude Code.
- 2026-10-04: Phase 2 runtime/API/worker/MCP/demo/console wired; model boundary and optional Git sandbox harness added. The original Phase 1 integration sources are preserved as `legacy_int_*.py` with a [transition mapping](phase1-test-transition.md). Seven new Phase 2 checks carry forward concurrent commit, receipt/event immutability, SSE, stale write, retry identity, and invalid action guarantees. An eighth check compares the console session's approval path with the agent bearer path. Full current backend suite: **147 passed** on the disposable PostgreSQL test database.
