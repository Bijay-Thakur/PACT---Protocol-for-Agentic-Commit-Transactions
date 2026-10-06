# Phase 2.1 implementation and acceptance record

Baseline: `225e7531fa99814d15f5a7a46ab0c6a0320d040f`. The prior [A01–A48 matrix](../phase2/implementation-checklist.md) is retained. This is a local, uncommitted implementation; nothing was pushed or published. `PASS` below means a named local check ran, `BLOCKED` means the required external surface was unavailable, and `NOT_RUN` means no direct execution evidence exists. Unit or simulator evidence does not stand in for live provider, browser, or isolation evidence.

## Work packages and findings

| Package | Findings | Implementation and observed limit |
|---|---|---|
| P21-00 | Baseline | Reviewed the prior architecture and reproduced baseline tests against a guarded disposable PostgreSQL database. |
| P21-01 | F1, F5 | Typed retry eligibility, first potentially sent attempt retention, current evidence and intent identity checks; persisted prepare generation and revision fingerprints. Controlled overlap tests pass. |
| P21-02 | F2, F4 | Versioned offboarding full refund and outcome requirements, approval authorization epochs and current grant checks. Regression tests pass. |
| P21-03 | F3 | Exact digest Python commit, revise/withdraw, scoped discovery, begin/delegate/accept request replay; independent Python and Node MCP protocol checks and real API repair/discovery tests pass. |
| P21-04 | F8, NVIDIA | Explicit Groq and Nebius profiles, closed proposal wire schema, typed errors, durable API/CLI proposal traces and admission limits. Mocked wire, live Groq preflight/smoke and paced evaluation ran; Nemotron live inference awaits its key. |
| P21-05 | F6 | Server-owned accepted intent and assembly, separate requester/approver, frozen Projection/v1, exact commit and observed receipt. Authenticated HTTP/DB/simulator flow and three real Edge browser journeys pass. The browser runs exposed and led to a receipt initiator rendering fix; revision and residual views are now usable. |
| P21-06 | F7 | Restricted Compose reference and disposable Git target. Static Compose validation and existing real bare-Git harness tests pass. Docker daemon and actual Code Council source unavailable; enforcement isolation remains unproven. |
| P21-07 | F8 | Pinned direct and constrained transitive dependencies, migration 0003, guarded test launcher, CI definitions, readiness, independent Python/Node MCP tests. A clean Windows Python install, local suites, Edge browser journey, and populated migration probe pass; clean Linux/hosted CI remains unverified. |

| Finding | Starting defect | Fix | Regression and observed result | Remaining limit |
|---|---|---|---|---|
| F1 | Stale retry text could authorize another send. | Typed current eligibility, first intent retention and UNKNOWN hold. | Unit retry tests and PostgreSQL stale-text test pass; zero extra provider calls. | Controlled provider-clock/transport race matrix not complete. |
| F2 | $1 could satisfy $143.27 offboarding eligibility. | Exact trusted amount, zero omission, digest-bound outcomes and policy version. | $1, unavailable fact, wrong currency and low authority reject; full refund commits and forced final mismatch is held. | Other currencies and real providers remain outside simulator evidence. |
| F3 | SDK lifecycle drift and missing repair/discovery. | Exact digest client, revise/withdraw, scoped discovery and replay IDs. | Begin/delegate concurrency, route/target isolation, Python client repair, scoped child discovery and Python/Node MCP protocol pass. | Hosted external client execution not run. |
| F4 | Role removal did not invalidate prior approval. | Current grant/role/scope and authorization epoch at commit and dispatch. | Remove/readd/no-op epoch and before/after durable intent revocation tests pass. | External provider outcome after revocation still relies on pinned contract. |
| F5 | Old read could publish into replacement prepare. | Durable generation plus revision/effect fingerprint fence. | Both A/B arrival orders, withdrawal, replacement, and B in a restarted runtime pass on PostgreSQL. | A process crash during a pending read was not directly injected. |
| F6 | Console stopped after intent review. | Acceptance, trusted assembly, frozen Projection, separate approval and exact commit; revise/reprepare controls and residual receipt display. | Authenticated HTTP/DB/simulator and three Edge browser flows pass; frontend lint/build pass. | Browser UNKNOWN reconciliation view was not directly exercised. |
| F7 | Protected target boundary was not enforced in a runnable deployment. | Restricted Compose reference and disposable Git target. | Compose syntax and bare-Git promotion/reconciliation pass. | Docker daemon and Code Council source absent; isolation unproven. |
| F8 | Dependency, CI and model evidence gaps. | Constraints, CI jobs, guarded DB, readiness, common API/CLI trace/admission. | 178 backend tests, clean Windows Python install, populated migration, frontend, Edge browser and Python/Node checks pass; cancellation/crash/cross-process model tests included. | Hosted CI unrun; live evaluation missed usefulness target. |

## Acceptance matrix

| Test | Status | Evidence or missing gate |
|---|---|---|
| T01 | PASS | `test_stale_textual_retry_permission_cannot_reach_provider`; dispatch now persists UNKNOWN hold. |
| T02 | PASS | `test_unit_phase21_regressions.py`: first potentially sent attempt anchors retention. |
| T03 | PASS | Retry evidence requires authoritative observation bound to latest attempt; unit regression. |
| T04 | PASS | Existing retry/reconciliation integration tests in full backend suite. |
| T05 | PASS | Full eligible refund regression in `test_unit_phase21_regressions.py`; accepted end-to-end refund in intent flow. |
| T06 | PASS | Zero omission, unsupported currency, unavailable fact and insufficient authority regressions pass; existing stale-fact commit test remains active. |
| T07 | PASS | Projection/outcome digest and final amount enforced; forced $1 final observation becomes HUMAN_REQUIRED with an APPLIED_MISMATCH residual. |
| T08 | PASS | Old frozen offboarding plan held before commit; an already queued plan retains pinned execution duty; populated 0002 → 0003 evidence probe passes. |
| T09 | PASS | Python client API surface and exact digest commit updated; backend tests. |
| T10 | PASS | Concurrent begin/delegate request replay and accept replay pass. |
| T11 | PASS | Changed body, route and target conflict; same request ID is isolated by principal; API auth and tenant checks pass. |
| T12 | PASS | Real separate-process Python MCP revise/withdraw repairs a frozen draft; Python SDK denies old digest and executing edits. |
| T13 | PASS | Delegated child sees its own workflow/contracts without root begin authority; parent cannot inspect child by child-scoped discovery. |
| T14 | PASS | Independent Node and Python MCP clients initialized, discovered, handled typed and malformed calls, disconnected and reconnected against stub HTTP. |
| T15 | PASS | Current principal/grant, role/scope, epoch, expiry, digest and self-approval checks in backend suite. |
| T16 | PASS | `test_int_phase21_fences.py` remove/readd and no-op grant epoch regression. |
| T17 | PASS | Controlled revocation before durable intent blocks send; revocation after intent retains the one in-flight provider action. |
| T18 | PASS | Controlled A/reset/B/A-return PostgreSQL test. |
| T19 | PASS | Controlled reverse arrival, withdrawal, replacement revision and successful prepare pass; a new Runtime for B verifies the persisted generation fence while A is pending. An actual process kill during A was not injected. |
| T20 | PASS | Real Edge browser: requester interpreted, reviewed, explicitly accepted, assembled and froze a synthetic offboarding request without admin privileges. |
| T21 | PASS | Real Edge browser: a separate approver approved, requester committed the exact digest, and the observed simulator outcome produced a final receipt. |
| T22 | PASS | Edge browser: requester approval denied, an approved plan revised and re-prepared, prior approval rejected until the replacement revision was reapproved, double-click commit queued one transaction, and a browser disconnect/reload still reached its receipt. Backend stale digest and replay regressions also pass. |
| T23 | NOT_RUN | Edge browser shows an applied $99.00 refund mismatch as HUMAN_REQUIRED with APPLIED_MISMATCH residual and recovery controls; backend UNKNOWN/recovery coverage passes. Browser UNKNOWN reconciliation view was not directly exercised. |
| T24 | PASS | Projection/v1 bound into frozen plan/digest; no synthetic provider ID. |
| T25 | PASS | End-to-end simulator flow verifies refund call and receipt after observed outcomes; Edge browser shows the full $143.27 requested amount and verifies the final receipt hash. |
| T26 | BLOCKED | Docker daemon unavailable; restricted agent could not actually be launched. |
| T27 | BLOCKED | Compose static config has separate protected network, read-only agent and no sensitive mounts; runtime denial unverified. |
| T28 | PASS | Existing disposable bare-Git harness tests included in full backend suite. |
| T29 | PASS | Existing lost-response and fresh-runtime Git reconciliation tests included. |
| T30 | PASS | Fresh Windows Python 3.14 virtual environment installed the constrained backend successfully; `pip check` and 14 selected unit tests pass. Local disposable-Postgres backend, frontend, Edge browser and independent Python/Node jobs pass. CI defines those jobs and a Docker reference probe; hosted execution is separately unrun. |
| T31 | PASS | `scripts/verify_populated_migration.py`: 0002 → 0003 preserved 123 receipts, 270 observations, 47 holds, plus seeded UNKNOWN/in-flight rows in a disposable DB copy. |
| T32 | PASS | Guarded disposable test runner and `/readyz` migration/DB check; demo remains opt-in. |
| T33 | PASS | Groq profile remains active in example/local setup; profile selection does not require Nebius key. Fixture is labeled. |
| T34 | PASS | Nebius default/model/secret commands and mocked wire conformance pass; live endpoint is the separate T42 gate. |
| T35 | PASS | Explicit profile selection and credential host checks; both-key selection and conflict tests. |
| T36 | PASS | Missing profile key, wrong host, mocked 401/403/404/429/400 schema capability, refusal and truncation map to typed errors; live Nemotron access is T42. |
| T37 | PASS | Closed wire parser and canonical proposal reject privileged/oversized/malformed fields; original-text customer mismatch now requires explicit correction before acceptance. |
| T38 | PASS | API and CLI use the same durable admission/trace path; failure, cancellation and stale-crash UNKNOWN_USAGE reservations verified. |
| T39 | PASS | PostgreSQL advisory lock and conservative reservation: two independent processes race with one allowance; exactly one is admitted. |
| T40 | PASS | Mocked Groq→Nebius profile switch persists both provider traces; accepted Nemotron proposal follows trusted assembly and compiler. Live Nebius is T42. |
| T41 | FAIL | Approved catalog smoke was reviewable with the right customer (1,108 tokens). Paced 50-case catalog evaluation returned 41 proposals, eight provider errors, one invalid output, 40 expected matches, 9/10 held-out matches, and 18/23 valid reviewable cases (78%, below 90% target). Two model customer mismatches prompted a new server-side blocker; the catalog corpus was not rerun after that fix. No protected execution occurred. |
| T42 | BLOCKED | Nebius key absent; mocked wire is not a live NVIDIA result. |
| T43 | BLOCKED | Code Council source absent; only reference Git harness verified. |
| T44 | PASS | Full backend suite includes all nine authenticated simulated demos and prior active A tests. |
| T45 | PASS | This record, setup and engine walkthrough below; boundaries stated explicitly. |

`NOT_RUN` on a partly exercised row means the full named gate has no complete execution evidence. `FAIL` means a run occurred and missed its acceptance target.

## Local validation commands

From the repository root with the existing Python environment and private `.env`:

```powershell
.\.venv\Scripts\python.exe scripts/run_disposable_tests.py backend/tests
.\.venv\Scripts\python.exe scripts/verify_populated_migration.py
.\.venv\Scripts\python.exe examples/mcp-python/protocol_test.py
cd frontend; npm.cmd ci; npm.cmd run lint; npm.cmd run build
cd ..; $env:PACT_BROWSER_CUSTOMER_ID='C-BROWSER-LOCAL-UNIQUE'; .\.venv\Scripts\python.exe scripts/provision_browser_fixture.py
# Start scripts/run_disposable_app.py and a frontend dev server on 13000 with NEXT_PUBLIC_PACT_API_URL=http://127.0.0.1:18000.
cd frontend; $env:PACT_BROWSER_CHANNEL='msedge'; $env:PACT_BROWSER_CUSTOMER_ID='C-BROWSER-LOCAL-UNIQUE'; npm.cmd run test:browser
cd ..\examples\mcp-node; npm.cmd ci; npm.cmd run protocol-test
cd ..\..\backend; ..\.venv\Scripts\python.exe -m app.cli model-check --profile groq_dev
..\.venv\Scripts\python.exe -m app.cli model-check --profile nebius_nemotron
```

The test launcher targets only `pact_phase21_test`, checks separation from the working database, and relies on the existing fixture guard before schema reset. The local full backend suite (178 passed), frontend lint/build, three real Edge browser journeys and independent Python/Node protocol tests passed by 2026-10-06. A fresh Windows Python 3.14 environment installed the constrained backend; `pip check` and 14 selected unit tests passed there. The populated migration probe passed. The Compose reference config parsed, but the Docker engine was unavailable. No owner database was reset. Both live Groq evaluations are recorded in ignored `.local/phase21-groq-eval.json` and `.local/phase21-groq-catalog-eval.json` with per-case sanitized outcomes and no business effects.

## Operator and model setup

`PACT_MODEL_PROFILE=groq_dev` selects Groq with `GROQ_API_KEY`; `nebius_nemotron` selects the Nemotron endpoint/model in `.env.example` with `NEBIUS_API_KEY`. Selection is explicit; a second key never switches the provider. Export the selected key and profile into the CLI process environment (the CLI does not load `.env` itself). Clear legacy `PACT_PLANNER_*` endpoint/model/key settings when switching to an explicit profile. Keep secrets in the private environment. `model-check --live` queries the selected provider's model listing; `model-smoke --max-calls 1` and `model-eval --dataset backend/tests/fixtures/model_intents_v1.jsonl --max-calls 50 --report <path> --delay-ms 10000` are bounded model probes that use the API's durable trace/admission path. `--share-workflow-catalog` explicitly sends the server-owned workflow catalog to the selected provider; the user approved this for the bounded run. Probe output is proposal/evaluation evidence, never a trusted PACT commit.

The engine path is: untrusted model interpretation → explicit requester clarification and acceptance → trusted workflow fact reads → draft proposals → policy compilation and frozen digest/Projection → independent current operator approval → exact digest commit → persisted dispatch intent → adapter → external observation/reconciliation → receipt or incident. The model cannot grant authority or execute effects. The offboarding workflow requires the entire trusted eligible refund and premium revocation; partial remediation is a separate workflow. Old frozen offboarding policy is held before dispatch, while already in-flight work keeps its pinned contract and reconciliation duty.

| Engine | Phase 2.1 decision and evidence |
|---|---|
| Transaction manager | Authenticated begin/delegate/revise/withdraw and request replay own the draft hierarchy and revision. |
| Authority and policy compiler | Current grants, capability attenuation, role epochs, trusted facts and required outcomes determine whether a revision can freeze and proceed. |
| Commit barrier and coordinator | Exact digest, current approval and dependency checks precede a durable execution intent. A stale prepare generation cannot publish evidence. |
| Executor and evidence ledger | The adapter receives the stable provider key only after intent persistence. Retry eligibility is recomputed from current attempts, contract and observations; ambiguity becomes UNKNOWN. |
| Verifier, reconciler and compensator | External observations establish application and postconditions, resolve uncertainty or retain a human obligation. An applied mismatch is never represented as a successful commit. |
| Receipt generator and projection | Projection records expected material consequences at freeze; the receipt cites verified observations or an incident/residual obligation. |
| Planner boundary | Groq or Nebius returns only a bounded proposal. Server review and explicit acceptance feed the same compiler and authority path as non-model drafts. |

The local `sim/` endpoints are simulated providers. The Git harness uses a real disposable bare repository but is a reference client, not the Code Council application. The Compose declaration alone does not establish arbitrary-agent isolation. No live Nebius model call, actual Code Council invocation, protected container denial probe or browser UNKNOWN reconciliation walkthrough was completed here.

For the reference boundary, run `scripts/create_reference_target.py` then supply a private disposable database password and use `deploy/reference/compose.yml` on a Docker host. Inspect agent denial and PACT-only promotion before claiming isolation. The actual Code Council repository must be integrated and exercised separately.
