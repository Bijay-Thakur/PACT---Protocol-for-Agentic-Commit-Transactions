# Next-phase requirements and acceptance register

Status is evidence-based: `PASS`, `FAIL`, `BLOCKED`, or `NOT_RUN`. Historical
Phase 2/2.1 results are linked but do not turn a current gate into `PASS`.
Machine-readable results live in `results.json`.

Completion label: **still incomplete**. This is not a reference
SDK-ready service, an externally validated product, or a production-ready
system. Human semantic qualification is blocked, and no hosted CI run or live
provider qualification was executed.

## Findings

- F01/F02 — implemented and tested: versioned semantic records, protected source
  persistence, trace-bound review, digest binding, and omission regressions.
- F03 — implemented: the deterministic judge is a separate advisory service.
  It cannot approve, freeze, or dispatch. False concerns, timeouts, forged
  citations, and stale generations hold without effects.
- F04 — partial: the 400-case packet, rubric, and local ablation exist. Human
  qualification remains blocked, so usefulness and unsafe-pass rates are not
  claimed against human labels.
- F05 — implemented seam: generic routes use the assembler and outcome
  registries. Offboarding and the disposable Git workflow register without a
  new core branch.
- F06 — tested: unchanged capability rewrites still dispatch once; narrowing a
  resource or amount before durable dispatch blocks; narrowing after durable
  intent cannot unsend.
- F07 — tested on disposable Postgres: process death before intent, after
  intent, and after the external call leaves one business effect and a verified
  receipt. Recovery does not call the judge.
- F08 — measured: the reference Compose stack denied the agent a direct write,
  database access, and provider access, then completed one approved
  PACT-mediated Git promotion. The host inspected the bare repository.
- F09 — implemented: OpenAPI includes adjudication, the convenience client
  matches digest and reconcile behavior, and the raw-HTTP fixture completed a
  verified receipt.
- F10 — implemented: task routes, tenant-scoped queues, and the Edge journeys
  for approval, revision, clarification, UNKNOWN reconciliation, mismatch, and
  receipt all passed.
- F11 — implemented: Phase 2.1 evidence is tracked, and this report separates
  local results from blocked human and live-provider gates.
- F12 — retained boundary: Git remains a disposable reference, business
  providers remain simulated, and receipts remain hashed rather than signed.
  Code Council and RivalScope are not integrated.

## Gates

- G01 `PASS` — baseline SHA and evidence types are recorded.
- G02 `PASS` — guarded disposable PostgreSQL `pact_phase21_test` was used. The
  working database was not the test target.
- G03 `PASS` — lost-intent regressions are in the 199-test disposable suite.
- G04 `PASS` — generated OpenAPI, replay, and client digest/recovery behavior
  agree. `/api/v1/planner/adjudicate` is in the snapshot.
- G05 `PASS` — request-id replay, changed-body conflict, and tenant-scoped
  reads passed in the disposable suite.
- G06 `PASS` — offboarding and `code_sandbox_change` share the core registries.
- G07 `PASS` — no-op, narrowed, and post-intent capability cases passed.
- G08 `PASS` — an old offboarding policy pin blocks commit until a new revision
  adopts the current policy.
- G09 `PASS` — populated 0002 to 0004 migration preserved receipts, evidence,
  attempts, residuals, UNKNOWN, and inflight rows.
- G10 `PASS` — duplicate effect-type adapter and workflow registrations now
  fail with structured ambiguity codes in both constructor and incremental
  registration tests. The supported configuration still has one account per
  provider/effect type.
- G11 `PASS` — principal-bound source review and trace substitution rejection pass.
- G12 `PASS` — entity, amount, currency, timing, contradiction, injection, and
  compiled-candidate comparison are regression-tested.
- G13 `PASS` — forged judge output and authority-shaped fields cannot pass.
  Caller bypass keys are rejected.
- G14 `PASS` — candidate, assessment, and final digests bind without a cycle.
  A stale judge generation does not freeze.
- G15 `PASS` — a generic note cannot release a concern. An issue-specific
  adjudication can, and it does not itself freeze or dispatch.
- G16 `PASS` — timeout persists an `UNAVAILABLE` hold, uses two calls, and a
  later prepare does not call the judge again.
- G17 `PASS` — natural-language candidates require the judge. Structured Git
  promotion still uses deterministic policy. `semantic_bypass` is rejected.
- G18 `PASS` — crash recovery does not call the judge and does not rewrite the
  pinned commitment.
- G19 `BLOCKED` — two independent human reviewers have not labeled the corpus.
- G20 `BLOCKED` — the 95/100 usefulness and zero unsafe-pass targets require
  those human labels. Generator-label scores are not that gate.
- G21 `PASS` — the legacy 50-case deterministic run remains a legacy regression
  only.
- G22 `NOT_RUN` — local ablation of 400 generator-labeled cases is not always-hold.
  A 40-case subset repeated three times was stable and did not inflate the
  denominator. Provider calls and tokens were zero because the judge was
  deterministic. The required 10-concurrent control-overhead p95 budget was
  not run; this gate cannot pass on the partial ablation alone.
- G23 `BLOCKED` — Groq and Nemotron were not called.
- G24 `PASS` — Docker 29.8.2 ran the reference stack. The healthy target started
  at one commit. The agent could not mount the repository, reach Postgres, or
  reach the simulators, and a direct file write failed. After a separate
  approver accepted the digest, the agent reached `COMMITTED_VERIFIED` and the
  host observed exactly two commits and the mediated file contents.
- G25 `PASS` — disposable Git CAS, lost-response, and one-refund provider cases
  passed.
- G26 `PASS` — process-death, worker fencing, and stalled-prepare tests passed.
- G27 `PASS` — mismatch and residual obligations remain explicit. The browser
  mismatch journey shows the residual receipt.
- G28 `PASS` — tenant-scoped overview, approval, and incident queries, filters,
  and cursor pagination passed.
- G29 `PASS` — approval, revision, and reconcile actions in the browser are
  server-authorized. The UI does not treat a provider response as
  `COMMITTED_VERIFIED` by itself.
- G30 `PASS` — six Edge journeys passed, including clarification, UNKNOWN
  reconciliation, mismatch, and receipt verification.
- G31 `PASS` — keyboard focus at 1280px reached Incidents, and the 390px width
  still showed primary navigation. Demo controls stay on `/demo`.
- G32 `PASS` — disposable Postgres suite: 205 passed, 0 failed, 0 skipped.
  All nine demo scenarios now assert independent simulator refund and subscription
  state. Browser suite: 6 passed in the preceding local run. Isolation: pass in
  the preceding local run. G22 remains explicitly unmeasured.
- G33 `NOT_RUN` — no hosted workflow was executed.
- G34 `PASS` — the separate-process raw-HTTP fixture completed an authenticated
  lifecycle and verified the receipt without service imports.
- G35 `PASS` — OpenAPI and the lifecycle, extension, and deployment documents
  match the implementation. No SDK package was created.
- G36 `PASS` — this register and the release report name the blocked gates.

## Non-pass policy

No required `FAIL` was converted to a skip. G22 and G33 were not run. G19, G20,
and G23 stay blocked. Missing human qualification prevents the label
“reference SDK-ready service.”
