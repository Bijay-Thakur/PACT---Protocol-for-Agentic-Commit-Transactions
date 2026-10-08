# Next-phase implementation report

Baseline: `7142a93c0a37790beb89cca42b10e6d7bec81375` on `main`. Completion
label: **still incomplete**.

Human reviewers have not qualified the 400-case corpus, the concurrent control
overhead gate has not been measured, and live provider qualification remains
open. This is not a reference SDK-ready service, an externally validated
product, or a production-ready system. No SDK was packaged. Code Council and
RivalScope were not imported or integrated.

## What passed locally

- Disposable PostgreSQL `pact_phase21_test`: 205 passed, 0 failed, 0 skipped.
  The working `pact` database was not the suite target.
- Independent deterministic judge: false concerns hold, issue-specific
  adjudication can release only the named issue, timeouts and forged citations
  stay unavailable, and a stale generation does not freeze. Inconsistent `PASS`
  records, unexplained unknowns, and dismissed nondismissable issues stay held.
- Duplicate effect-type adapters and workflow keys are rejected at registration;
  deployment order cannot silently choose a provider/account owner.
- Authority: a narrowed grant blocks pending forward dispatch; an unchanged
  rewrite dispatches once; a change after durable intent cannot unsend.
- Recovery: crashes before intent, after intent, and after the external call
  leave one business effect and `COMMITTED_VERIFIED` without a judge call.
- All nine demo scenarios assert the expected independent simulator refund and
  subscription state in addition to the PACT transaction state.
- Docker 29.8.2 reference stack: the isolated agent had no Git mount, database
  credential, or route to Postgres or the simulators. A separate approver
  accepted the digest. PACT promoted one file. The host read the bare
  repository and saw the commit count go from 1 to 2.
- Edge browser, customer `C-RUN-223413`: receipt, revision, mismatch residual,
  contradictory clarification, UNKNOWN reconciliation, and 1280/390 keyboard
  navigation. 6 passed.
- OpenAPI snapshot regenerated with `/api/v1/planner/adjudicate` (36 paths).
- Local ablation on generator labels, not humans: 400 cases; holds 178 / 114 /
  114 across deterministic, proposer-echo, and proposer-plus-judge modes;
  always-hold is false; 40 cases repeated 3 times stayed stable. Provider
  calls and tokens were 0.
- Frontend lint and production build passed on 7 October 2026. The Docker and
  six Edge browser results above are from the preceding local implementation
  run and were not rerun during this audit.

## Still blocked or not run

- G19 and G20: no two human reviewers and no human adjudication. The clean-pass
  count on generator labels is not the 95/100 usefulness gate.
- G23: Groq and Nemotron were not called. No business effect was created by a
  live model.
- G33: hosted CI was not run.
- G22: the 10-concurrent control-overhead p95 budget has not been measured.

## Limits

Business providers in these runs are simulated. The Git target is a disposable
bare repository, not Code Council. Receipts are hashed, not signed. Demo mode
is enabled only for the disposable browser host, not as the service default.
