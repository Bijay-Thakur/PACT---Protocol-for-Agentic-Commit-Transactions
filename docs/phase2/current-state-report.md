# PACT build state and change report — 2026-10-04

This report compares the working tree with commit `0b4bc3f` (`Added Logo, transform UI ito soft neumorphism style`). It treats [the owner-shared Hackathon conversation](https://chatgpt.com/share/6ac31798-7a38-83ea-afc3-33d045c4e2b7) and the local [Phase 2 plan](../../PACT_Phase_2_Build_Plan_and_Master_Prompt.md) as project context and specifications. Claims of completion below come from code and checks in this checkout. No Phase 2 changes have been committed or pushed.

## Intent recovered from the shared conversation

The owner wants PACT to sit beside MCP and govern protected writes by multiple agents. The chosen flagship workflow is customer offboarding and refund, where subscription, billing, access, CRM and notification must agree. Code Council is the first proposed external client; RivalScope is second. The hackathon target calls for live NVIDIA model use through Nebius, with a longer term goal of demonstrating a useful product to outside teams. The shared conversation also cautions against claiming distributed atomicity where providers cannot supply it, or treating an LLM proposal as authority. Those are goals and design constraints, not evidence that the integration or deployment is complete.

## What changed since the last commit

Git currently shows 62 modified/deleted tracked paths plus new Phase 2 files. The tracked diff is roughly 4.3k added and 2.6k removed lines, excluding untracked files. The five deleted `test_int_*.py` paths have their original sources preserved as `legacy_int_*.py`; their old anonymous API assumptions no longer match Phase 2. [The test transition](phase1-test-transition.md) maps old behavior to new tests. There is no intermediate commit that cleanly separates Claude Code's initial v2 receipt work from the continuation, so per-line authorship cannot be established from Git. Claude's receipt generator was retained and extended as needed for the new coordinator and evidence model.

| Area | Current working-tree changes |
|---|---|
| Domain, schema, migration | New application/postcondition/restoration dimensions, observation and residual rows, principal/session/credential rows, approvals, plan revisions, resource reservations, budget ledger and durable work items. Additive migration `0002` retains Phase 1 receipt data. |
| Trust and planning | Authenticated agent API keys and operator cookie sessions with CSRF; trusted workflow registry, mandatory policy, bounded model `PlanProposal`, compiled digest and digest-bound approvals. Model output cannot assign identity, policy or permission. |
| Commit and execution | Coordinator lifecycle now handles prepare, freeze, approval, binding barrier, reservation/budget holds, work queue, worker leases and fencing, reconciliation, compensation, sweeper and operator actions. Logical business operation identity prevents a caller from evading deduplication by changing its key. |
| Adapters and evidence | Versioned contracts, pinned provider preconditions and final observations distinguish applied, satisfied, restored, unknown and residual states. Adverse replies and lagging reads are not treated as clean success. Receipt v2 cites final evidence, plan and contract pins while old v1 receipts remain verifiable. |
| Interfaces | Authenticated REST, operator console login and intent review, MCP stdio server on the official Python SDK, Python convenience client and pinned Node client example. Nine simulator scenarios remain available through the authenticated demo console and CLI. |
| First external harness | Optional `code_sandbox_change` workflow with a disposable bare Git repository, path allowlist and atomic sandbox-ref promotion. It is a harness; actual Code Council source has not run. |
| Planner and local operation | Groq provider uses private `GROQ_API_KEY` as the fallback key, opt-in trusted workflow catalog grounding, unknown workflow clarification, and a safe CLI error. `.env.example`, Compose settings, PowerShell start/stop scripts and current run instructions were added or updated. `.local/` is ignored. |
| Tests and docs | Phase 2 integration and adverse-case tests, disposable DB guard, Phase 1 migration fixture, 50-intent fixture, acceptance checklist, runbook, MCP guide, demo script and validation report. |

## How the engine now works

An authenticated agent or operator submits a business intent. The optional model returns only a bounded proposal. PACT's trusted workflow registry validates business fields and supplies mandatory actions, policy and authority from the authenticated principal. During prepare, read-only provider facts and contract versions are pinned into a frozen plan digest. An operator approval, when required, names that exact digest. The binding barrier checks invariants and obtains resource and budget reservations in one PostgreSQL transaction. A durable worker then sends each effect, observes provider reality, reconciles uncertain responses by business operation identity, and either verifies the required outcome or records compensation and residual obligations. The final receipt cites the immutable plan and observations. This is coordination with explicit uncertainty; it cannot make unrelated external APIs physically atomic.

## What is verified

- The complete current backend suite passed **149 tests** against a newly created empty `pact_test` database on local PostgreSQL 16. The fixture's destructive schema reset was confined to `pact_test`; it did not target the owner's `pact` database. Without a test DB, **89 passed, 60 skipped**. An earlier complete run before the latest Groq changes passed 147 tests on a separate disposable PostgreSQL server at port 55432. The Groq fallback/catalog and unknown-workflow cases are included in the 149-pass result.
- Frontend ESLint and TypeScript checks pass now. The Next.js production build passed earlier in this continuation; a browser end-to-end walkthrough is still open.
- The local PostgreSQL 16 service was running, but the database named in the owner's `.env` did not exist. A new `pact` development database was created and migrated additively; no prior `pact` data was present to overwrite.
- `scripts/start_local.ps1` loads `.env`, runs migrations and starts the embedded-worker API and Next.js console. `GET /healthz` and the console root both returned HTTP 200 after a restart with the current code. The scripts use a process-only PowerShell execution-policy bypass and write ignored logs and process records to `.local/`.
- A live Groq `openai/gpt-oss-20b` smoke call returned a bounded `customer_offboarding` proposal with exact action slots, customer `C-123`, a question for the missing reason, token usage and `applied=false`. A first unguided response had invented workflow `CancelCustomer`; review now returns `NEEDS_CLARIFICATION` for that case. The owner explicitly approved sending the registered workflow catalog (descriptions, field names, action slots) to Groq, and the guided call used it successfully. The key stayed in ignored `.env` and was not printed or sent to the frontend.
- The [validation report](validation-report.md) gives the earlier real-Postgres evidence: MCP independent clients, process-death recovery, two-worker fencing, adverse provider responses, v1 migration, nine demos and a ten-request valid sample. The [A01–A48 matrix](implementation-checklist.md#acceptance-matrix) records 43 passed checks, one partial (A32), two blocked (A39/A40), and two not run (A43/A46). These are bounded test results, not a production safety certification.

## How to run this checkout now

If the API and console are already running, first stop them. Then create the operator account with a password you choose:

```powershell
cd D:\Projects\PACT
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_local.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_local.ps1 -SetupOperator
```

Open `http://localhost:3000` and sign in with tenant `local`, username `operator`, and your chosen password. The API docs are at `http://localhost:8000/docs`. Subsequent starts can omit `-SetupOperator`. If dependencies are missing, follow the [runbook](runbook.md). The launcher enables simulator demos only for this local run. Direct Python commands do not automatically load `.env`.

## What remains before the stated goal

1. **Nebius/NVIDIA A39:** Groq proves the external planner path, but the hackathon model requirement calls for a live Nebius/NVIDIA inference and recorded review evidence. Its endpoint, model and private key have not been configured here.
2. **Code Council A40 and isolation A43:** only the disposable Git harness has run. The real Code Council repository, actual MCP protected-write hook and a deployment where agents cannot bypass PACT's protected path are still needed. RivalScope follows later.
3. **Console A32/A46:** the REST/MCP authorization path and production frontend build are checked, but a complete browser-driven intent → review → approval → execution → incident/receipt run has not been observed.
4. **Packaging and external providers:** Docker Compose was not exercised because the local daemon is unavailable. Current verified business effects use simulated providers. Production adapters, operational hardening, signatures and a representative outside-client sample remain further work.

The current achievement is a substantially implemented and tested Phase 2 control engine with working local startup and a live restricted Groq planner. It is still a local prototype for the hackathon and is not evidence of full real-world enforcement or hackathon completion.
