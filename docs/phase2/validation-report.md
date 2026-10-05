# Phase 2 validation report — 2026-10-04

This report covers the work resumed after Claude Code stopped at the v2 receipt generator. The attached plan is an implementation specification; claims below are based on this checkout and commands actually run. No code was committed, pushed, deployed or merged. All external provider flows used local simulated services. The Code Council repository and live Nebius credentials were absent.

## Verified commands and results

Latest verification (2026-10-04): after configuring the owner's private Groq key and fixing test fixture planner selection, `python -m pytest -q -p no:cacheprovider --basetemp=.pytest_tmp backend/tests --tb=short` passed **149 tests** against a newly created, disposable `pact_test` database on local PostgreSQL 16. The working `pact` database was not reset. A live Groq `openai/gpt-oss-20b` smoke returned a restricted `customer_offboarding` proposal with `applied=false`; this is separate from the still-blocked live Nebius/NVIDIA A39. The current PowerShell launcher started the API and console, and both roots returned HTTP 200.

| Check | Result |
|---|---|
| Phase 2, migration, unit and Git harness tests on disposable PostgreSQL 18.4 at `localhost:55432/pact_test` | Earlier focused run: `pytest -q -p no:cacheprovider --basetemp=.pytest_tmp -k 'phase2 or migration_upgrade or unit or git_sandbox_harness'` → **139 passed, 40 deselected**. |
| Full backend suite on the same disposable DB | `python -m pytest -q -p no:cacheprovider --basetemp=.pytest_tmp --tb=short` using the project venv → **147 passed**. The five original Phase 1 integration modules are preserved as `legacy_int_*.py`; their obsolete anonymous API harness is retired. [Transition mapping](phase1-test-transition.md) identifies the Phase 2 replacements and remaining differences. |
| Frontend | `npm.cmd run lint`, `npx.cmd tsc --noEmit`, `npm.cmd run build` → passed. Next.js 16.3.8 production build completed. Browser walkthrough not run: the computer-use runtime was unavailable and local Edge headless exited during startup. |
| MCP | `test_int_phase2_mcp.py` launches a real API and separate SDK stdio server; Python and pinned Node 2.3.0 clients initialize, discover and call tools. Malformed arguments are rejected, a second agent key cannot read the first agent's transaction, and disconnect/replay after QUEUED produces one refund. |
| Durable recovery | Local CLI `crash-midflight` terminated after the simulated billing call; `recover` resumed the persisted root to `COMMITTED_VERIFIED` on the disposable DB. Before/after intent, stale late-response, stalled child PREPARING, RECONCILING lookup, COMPENSATING inverse call, receipt-write rollback, and two-worker lease/fencing probes pass. |
| Git sandbox | Eight real disposable bare-repo tests passed: path/conflict/evidence checks, stale base, CAS race, lost-response ref observation, optional runtime registration, and two PACT approval → restarted runtime → worker → observed ref cases (normal and lost response). No actual Code Council code ran. |
| Model | Five planner boundary/API tests pass; 50-intent fixture corpus has 10 held-out cases. `planner-smoke 'Cancel customer C-123'` returned a labeled deterministic proposal with `applied=false`. No live Nebius inference. |
| Migration | Populated Phase 1 fixture upgraded additively; v1 receipt digests remained valid, legacy uncertain roots stayed quarantined and no work/approvals were synthesized. Test DB guard refuses the configured working database. |

The test server briefly stopped during a broad run, causing setup connection errors. It was identified as Claude's disposable PostgreSQL instance in a temporary scratchpad and restarted on port 55432. The subsequent focused and full runs above completed; no working database was reset.

## What is implemented

- Authenticated REST transactions, operator sessions with CSRF protection, server-owned policy packs, plan revisions/digests, digest-bound approvals and the atomic reservation/budget barrier.
- A cookie-session approval test confirms CSRF enforcement, digest binding, rejection of agent approval, and commit by the initiating bearer-authenticated agent; combining a cookie and bearer key on one request is rejected.
- Durable work items, leases/heartbeats, sweeper and a standalone or embedded worker. Receipt v2 preserves observed application, postcondition, restoration and residual truth; v1 receipt storage remains intact.
- Real Postgres worker fencing tests show two live workers cannot claim one item, an expired epoch cannot finish it, and a late provider response is stored as non-authoritative evidence before the new worker reconciles the single refund.
- Adverse provider tests preserve wrong-value, wrong-charge, duplicate, partial, wrong-recipient and wrong-content application as mismatches with residuals. Apply-then-503 and lost-response refunds reconcile with one provider call. Delayed empty reads remain UNKNOWN within the declared consistency window. A renamed caller key cannot repeat a verified business operation; a changed payload conflicts, and an authorized new epoch gets a distinct identity. Predicate tests require one same-customer source and reject missing target fields.
- Real Postgres reservation tests cover aggregate/child resource compatibility. An UNKNOWN refund retains budget across worker lease takeover and terminal failed closure; an observed refund converts its hold to consumed spend. Compensation tests show existing absent entitlements are not re-granted, later provider edits block stale commit/restoration, and lost response or worker death after an inverse call does not cause a second inverse call. A forced receipt write failure rolls back the terminal state and succeeds on retry.
- A committed receipt cites the same final observation IDs and evidence digests used by final verification. A later operator attestation creates a linked amendment while leaving the original adverse receipt hash and payload untouched; populated Phase 1 v1 receipts remain verifiable.
- A nine-tool MCP stdio facade using the official Python SDK, an authenticated Python REST convenience client, and a pinned Node MCP smoke client.
- An opt-in authenticated nine-scenario simulator and console with operator login, live transaction display and a read-only intent interpretation/review panel.
- A bounded, strict model proposal schema with a failing-closed configured provider. Usage is reduced to token counts; request ID and latency are recorded without credentials. Model proposals do not confer authority or execute effects.
- An optional `code_sandbox_change` workflow against a configured **disposable** bare Git repo and path allowlist. It freezes a candidate tree/evidence digest and requires `code_approver` approval before atomic promotion to a dedicated sandbox ref. Its static checks do not execute arbitrary code.

## Remaining gates

The [acceptance matrix](implementation-checklist.md#acceptance-matrix) lists A01–A48 individually. The remaining local acceptance gaps are a browser walkthrough and console parity with REST/MCP/SDK for the same identity and policy path. The Phase 1 integration sources and their replacement coverage are documented in the [test transition](phase1-test-transition.md); the full current backend suite passes.

Live A39 needs the owner's Nebius/NVIDIA endpoint, model and private API key configured in the environment. A40 actual Code Council integration needs its repository and tool-call boundary. A43 deployment isolation needs an enforcement environment in which agents cannot access the configured Git path or credentials. The current Git result is **HARNESS_VERIFIED / REAL_PROJECT_NOT_RUN**. Docker Compose packaging was not exercised because the local Docker daemon is unavailable.

The deterministic 50-intent fixture confirms 23 valid cancellation requests produced restricted proposals, including 3 held-out valid examples, but it does not assess live Nemotron quality. Wrong-value and conflicting intents currently produce proposals for later trusted review; the fixture is deliberately narrow and is not a claim that those intents are safe to execute.

The policy false-block sample ran ten valid simulated requests through prepare, binding barrier and worker completion: six remediation requests with varied amounts and charge profiles, plus four offboardings. All ten reached `COMMITTED_VERIFIED` (0/10 false blocks in this small sample). This does not estimate behavior on an external provider or a broader business population.
