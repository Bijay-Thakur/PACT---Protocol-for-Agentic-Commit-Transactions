# PACT frontend redesign delivery — 8 October 2026

## What changed

The default `/` route is now the public PACT overview. The public site uses the
supplied visual direction: a dark editorial hero, the actual PACT logo mark,
blue actions, restrained terminal and code panels, light explanatory sections,
and responsive navigation. The narrative centers on a protected semantic
transaction boundary and distinguishes intent, authority, durable dispatch,
observation, and contract-dependent compensation.

The operational overview moved to `/console`. All other operational URLs and
their existing components remain in place. The shared shell keeps public pages
open and operational pages behind the existing `AuthGate`. The product page's
recent-run preview requests tenant-scoped data from the existing REST client
only when a session is available. The public Kernel is labelled illustrative;
the Kernel inside `/tx/[id]` displays recorded event data from
`useTransactionLive`.

No backend service, schema, migration, API response, model configuration, or
transaction execution code changed.

## Route map

| Route | Role |
|---|---|
| `/` | Public overview and illustrative Kernel |
| `/semantic-transactions` | Transaction model and guarantee limits |
| `/product` | Console overview and signed-in live-run preview |
| `/integrate` | Verified REST, in-repository Python client, and MCP examples |
| `/compare` | Qualified architectural comparison |
| `/docs` | Local quickstart, lifecycle, recovery, and interface guide |
| `/faq` | Answers about guarantees, deployment, and current status |
| `/get-started` | Repository and local setup path; no false access form |
| `/console` | Authenticated operational overview, previously at `/` |
| `/transactions`, `/requests/new`, `/approvals`, `/incidents`, `/receipts` | Preserved operational routes |
| `/tx/[id]`, `/tx/[id]/receipt` | Preserved deep links, inspector, and receipt |
| `/demo` | Original nine-scenario launcher and fault controls |

## Feature preservation

| Before | After | Evidence |
|---|---|---|
| Root operational overview | `/console` with the same `OverviewMetrics` and `TransactionList` | Loaded live counts and rows in browser |
| Request, clarification, assemble, prepare, approval, commit | Existing routes and service calls | End-to-end browser journey passed |
| Revision invalidation and exact-digest approval | Existing inspector and approval controls | Browser journey passed |
| Plan graph, effects, external reality, invariants, authority | Existing `/tx/[id]` panels | Inspector remained in passing journeys |
| Event timeline and receipt/hash verification | Existing detail and receipt views; live Kernel added | Browser receipt journey passed |
| Incident reconciliation and residual obligations | Existing `/incidents` and operator actions | Browser UNKNOWN and mismatch journeys passed |
| Nine simulator scenarios | Existing `ScenarioLauncher` at `/demo` | Every card launched through the UI and reached its expected recorded state |

## Integration status

Status terms describe the **frontend connection**, not a new backend promise.

| Capability | Status | Boundary |
|---|---|---|
| Transaction Manager | Connected | Console, transaction details, approval and commit controls use existing REST client. |
| Semantic Validator | Connected | Existing trace-bound assessment and issues appear on request and transaction views; public copy explains advisory model output. |
| Policy Engine | Connected | Existing barrier, invariant, and authority views remain server-backed. |
| Plan Graph | Connected | Existing `EffectDag` uses transaction detail effects and dependencies. |
| Checkpoints / State Store | Preserved in Demo | Backend state and event history remain; no new dedicated checkpoint editor or claim of one. |
| Rollback / Compensation | Connected | Existing incident, recovery, and receipt views remain; no universal reversal claim. |
| Audit / Provenance | Connected | Recorded events, external observations, receipts, and hash verification remain. |
| Python SDK | UI Prepared | An in-repository `HttpPactClient` example is shown. No standalone SDK package is published. |
| REST API | Connected | Existing versioned `/api/v1` client and operational controls remain. |
| MCP | UI Prepared | The existing stdio server and agent-safe boundary are documented; the browser does not invoke MCP. |
| GROQ / NVIDIA configuration interfaces | Not Verified | Groq development profile and Nebius/Nemotron wiring are described; live provider qualification was not attempted. |

## Verification

Baseline before edits: `npm run lint` and `npm run build` passed; the guarded
disposable backend suite had **205 passed**. No baseline browser screenshot was
captured, as recorded in `BASELINE_AUDIT.md`.

Final results:

- `npm run lint` — passed.
- `npm run build` — passed; all new public routes and preserved operational routes compiled.
- `.venv\Scripts\python.exe scripts\run_disposable_tests.py` — **205 passed** against guarded `pact_phase21_test`.
- `public-site.spec.ts` — **5 passed**: desktop/tablet/mobile routes, no page overflow or runtime page errors, navigation, tabs/copy, FAQ, and internal links.
- `intent-journey.spec.ts` — **6 passed** against a fresh synthetic customer in the disposable fixture: request/approval/receipt, revision invalidation, mismatch, clarification, UNKNOWN recovery, and keyboard navigation.
- `preserved-console.spec.ts` — **2 passed**: authenticated console/demo and all **nine** demo cards with expected states.
- `git diff --check` — passed. The final diff contains no backend or migration edits.

The initial preservation-suite retry failed because a previously completed
customer was reused, leaving its subscription cancelled. A fresh disposable
customer resolved that state conflict. The browser suite also exposed an
ambiguous accessible name on the new brand link; it was corrected before the
final passing run. The normal development service on ports 3000/8000 stayed
running; isolated browser servers on 13000/18000 were stopped after testing.

## Screenshots

The captured PNGs are under [`screenshots/`](screenshots/). The public route
test can refresh them with `PACT_CAPTURE_SCREENSHOTS=1`; routine test runs do
not overwrite them. All eight pages were captured at desktop (1440 px), tablet
(820 px), and mobile (390 px). The authenticated console and Demo Mode were
captured at desktop width.

| Page | Desktop | Tablet | Mobile |
|---|---|---|---|
| Overview | [PNG](screenshots/overview-desktop.png) | [PNG](screenshots/overview-tablet.png) | [PNG](screenshots/overview-mobile.png) |
| Semantic Transactions | [PNG](screenshots/semantic-transactions-desktop.png) | [PNG](screenshots/semantic-transactions-tablet.png) | [PNG](screenshots/semantic-transactions-mobile.png) |
| Product | [PNG](screenshots/product-desktop.png) | [PNG](screenshots/product-tablet.png) | [PNG](screenshots/product-mobile.png) |
| Integrate | [PNG](screenshots/integrate-desktop.png) | [PNG](screenshots/integrate-tablet.png) | [PNG](screenshots/integrate-mobile.png) |
| Compare | [PNG](screenshots/compare-desktop.png) | [PNG](screenshots/compare-tablet.png) | [PNG](screenshots/compare-mobile.png) |
| Docs | [PNG](screenshots/docs-desktop.png) | [PNG](screenshots/docs-tablet.png) | [PNG](screenshots/docs-mobile.png) |
| FAQ | [PNG](screenshots/faq-desktop.png) | [PNG](screenshots/faq-tablet.png) | [PNG](screenshots/faq-mobile.png) |
| Get Started | [PNG](screenshots/get-started-desktop.png) | [PNG](screenshots/get-started-tablet.png) | [PNG](screenshots/get-started-mobile.png) |

[Authenticated console](screenshots/console-desktop.png) · [Preserved Demo Mode](screenshots/demo-desktop.png)

## Remaining limits

- Live model-provider qualification, production provider adoption, and a
  published Python SDK remain outside this frontend change.
- The public signed-out Product preview deliberately shows a sign-in prompt;
  it never displays invented transaction data or performance percentages.
- The reference demo uses simulated business providers. Recovery guarantees
  depend on registered effect contracts, provider observation, and deployment
  isolation. A failed compensation may leave `HUMAN_REQUIRED`.
- The pre-redesign UI was not captured in a browser before edits; its baseline
  is a source, build, route, and backend-test audit.
