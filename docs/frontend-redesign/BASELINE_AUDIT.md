# Frontend redesign baseline — 8 October 2026

Revision before changes: `56c0b007a25452b95af75f1ad74b8c262dbde090`.
The worktree contained only the eight untracked reference screenshots under
`frontend/public/Assets/UI images/`.

## Stack and verified checks

- Next.js App Router 16.3.8, React 19.2.8, Tailwind CSS 4, Playwright 1.63.
- `npm run lint`: pass; `npm run build`: pass, 10 routes.
- `\.venv\Scripts\python.exe scripts\run_disposable_tests.py`: 205 passed,
  0 failed, 0 skipped against guarded `pact_phase21_test` PostgreSQL.
- The existing browser test file covers six journeys, but was not rerun for
  this baseline. Existing screenshot assets are design references, not captures
  of the running pre-redesign UI.

## Existing routes and preservation map

| Existing feature | Frontend location | Backend dependency | Baseline status | Preservation strategy |
|---|---|---|---|---|
| Overview and counts | `/` | `GET /api/v1/transactions/queues/overview` | Built and type checked | Move presentation to `/console`; retain live query |
| Transaction history | `/transactions` | `GET /api/v1/transactions` | Built and type checked | Keep URL and service client |
| Natural-language request | `/requests/new` | planner propose/review/accept, assemble, prepare | Built and type checked | Keep URL, auth, clarification flow |
| Approvals | `/approvals` | approval queue and exact-digest approve | Built and type checked | Keep URL and server authority |
| Incidents | `/incidents` | incident queue and operator actions | Built and type checked | Keep URL and server authority |
| Receipts | `/receipts`, `/tx/[id]/receipt` | receipt and hash verification | Built and type checked | Keep URLs and receipt component |
| Transaction inspector | `/tx/[id]` | detail, events, decision, external state | Built and type checked | Keep URL, graph, timeline, semantic and policy panels |
| Plan graph | `/tx/[id]` `EffectDag` | effect dependencies from detail | Built and type checked | Reuse existing graph |
| Semantic assessment | `/requests/new`, `/tx/[id]` | trace-bound review and revision assessment | Built and type checked | Keep issue and assessment display |
| Policy and authority | `/tx/[id]` | barrier, invariants, grants | Built and type checked | Keep inspector and authorization checks |
| Compensation/recovery | `/tx/[id]`, `/incidents` | operator actions, worker/reconciler | Built and type checked | Keep controlled actions and incident state |
| Audit/provenance | `/tx/[id]`, receipt | event and evidence APIs | Built and type checked | Reuse recorded events and receipts |
| Nine simulator scenarios | `/demo` | demo scenario endpoints; guarded by demo mode | Built and type checked | Preserve launcher and each scenario; keep demo route |

The root layout currently places every page behind `AuthGate`; `/` is the
operator overview. The redesign will make `/` public while retaining the
authenticated operational paths above. Browser session state uses a server
cookie and a CSRF token kept in session storage by `frontend/lib/api.ts`.

## Service boundary and product claims

The frontend calls authenticated REST `/api/v1` endpoints. The backend owns
PostgreSQL state, effect contracts, policy, authority, dispatch, reconciliation,
and receipts. MCP is a separate transport over the same service. There is an
in-repository Python `HttpPactClient`, but no published Python SDK or automatic
interception of arbitrary agent tools. Groq and Nebius/Nemotron are configured
model profiles, not proof of live qualification. The public site must not imply
universal rollback, global ACID semantics, live partner endorsements, or
production readiness.

## Baseline limitations

This source/build audit did not capture an authenticated pre-redesign browser
screenshot or rerun the browser journeys. Those are separate from the 205-test
backend baseline and must be called out in the final verification report.
