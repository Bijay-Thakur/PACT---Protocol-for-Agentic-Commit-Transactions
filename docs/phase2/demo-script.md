# Phase 2 reproducible demonstration

All provider and repository mutations in this script are simulated or disposable. No production refund, notification, merge or deployment is part of this demonstration.

## Business transaction

1. Follow [the runbook](runbook.md) to migrate a **disposable** development database, create an operator and agent, and start the API, worker and console. Enable `PACT_DEMO_MODE=true` for the simulator scenarios.
2. Sign in at `http://localhost:3000`. In **Interpret an intent**, enter `Cancel customer C-123`. The provider label is `deterministic_fixture` unless a live model was configured. Show the read-only business request and required slots; the panel does not execute a transaction.
3. Run the `success` scenario. Open the transaction and show the delegated agents, frozen plan digest, local preparations, binding barrier, provider dispatches, independent observations and final receipt. The root should end `COMMITTED_VERIFIED`.
4. Run `unknown` with the pause control. Show `UNKNOWN` after the simulated response loss, the provider's one recorded refund, and operator reconciliation. The final receipt must reflect the observed application rather than treating the lost response as a failure or retry instruction.
5. Run `verification-mismatch` and `compensation-failure`. Show the difference between accepted provider responses and observed postconditions, then the residual obligation for anything PACT could not restore.

For a command-line replay without the browser, run `..\.venv\Scripts\python.exe -m app.cli scenario success unknown compensation-failure` from `backend` against a disposable database. The console and CLI use the same application services. The nine scenario cases are tested by `test_int_phase2_demo.py`.

## Independent MCP and code sandbox proof

1. Provision one API key for an agent with the `code_sandbox_change` workflow grant and a configured disposable bare repo. The grant shape is `{"workflows":{"code_sandbox_change":{"allowed_repos":["council_fixture"]}}}`. Keep the key in the MCP server process environment.
2. Use the pinned [Node example](../../examples/mcp-node/client.mjs) to show real MCP initialize, tool discovery and authenticated contract listing. The Python MCP integration test also checks a second agent cannot read the first one's transaction.
3. Run `pytest -q -p no:cacheprovider --basetemp=.pytest_tmp tests/test_int_git_sandbox_harness.py` from `backend` with `PACT_TEST_DATABASE_URL` pointing to a guarded disposable Postgres DB. The test creates its own temporary bare Git repository, prepares an allowlisted candidate, freezes a PACT revision, requires operator approval, promotes with `git update-ref` compare-and-swap and independently reads the ref. Separate cases show stale base, conflicting patches, evidence changes and response-loss reconciliation.

This is **HARNESS_VERIFIED / REAL_PROJECT_NOT_RUN**. The actual Code Council repository was not available in the workspace, and the static fixture checks are not a sandboxed test runner for arbitrary candidate code. Live Nemotron inference is a separate A39 gate and requires the owner's configured credentials.
