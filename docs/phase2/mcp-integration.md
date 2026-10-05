# Phase 2 MCP and disposable Git integration

## Agent identity and tool calls

Start PACT and migrate the database using [the runbook](runbook.md). Provision each agent with a separate `create-agent` API key and only the workflow grant and scopes it needs. Run one `python -m app.mcp_server` stdio process per key, with `PACT_MCP_API_KEY` in that process's private environment and `PACT_API_URL` pointing to PACT. The MCP server forwards to the authenticated REST API. An agent cannot choose its principal in tool arguments. Operator approval uses a separate login/session and the `/approve` API route; it is not an MCP tool.

The nine tools are `pact_list_contracts`, `pact_begin`, `pact_delegate`, `pact_propose`, `pact_prepare`, `pact_request_commit`, `pact_status`, `pact_receipt`, and `pact_abort`. `pact_propose` requires a stable `request_id`; reuse the same value only for an exact replay. `pact_request_commit` requires the exact frozen revision digest returned by `pact_prepare`. A `QUEUED` result means the durable worker has accepted work, not that the external action happened. Poll `pact_status` and fetch `pact_receipt` only after finality.

All tools return an object with `ok`. On REST rejection, inspect `category`, `http_status`, `code`, and `message`; a transport failure returns `category=TRANSPORT`. A successful MCP protocol call can still return `ok=false` for a business rejection. A principal can see only its own transaction tree unless explicitly granted operator read scope.

## Node client smoke

The pinned Node SDK example is in `examples/mcp-node` and invokes real MCP initialize, discovery, and a contract call. From PowerShell:

```powershell
cd D:\Projects\PACT\examples\mcp-node
npm.cmd ci
$env:PACT_BACKEND_DIR = 'D:\Projects\PACT\backend'
$env:PACT_PYTHON = 'D:\Projects\PACT\.venv\Scripts\python.exe'
$env:PACT_API_URL = 'http://127.0.0.1:8000'
$env:PACT_MCP_API_KEY = '<private key for this one agent>'
npm.cmd run smoke
```

The Python `app.agents.client.PactClient` convenience class provides `begin`, `delegate`, `propose`, `prepare`, `request_commit`, `status`, `receipt`, and `abort` over the same authenticated REST API. Pass its `api_key` constructor argument and a caller-owned `httpx.AsyncClient`.

## Code Council hook contract

The real Code Council repository is unavailable in this workspace. The integration point in Code Council should create an agent principal with the `code_sandbox_change` workflow grant, call `pact_begin` with `{repo_id, base_commit}`, then call `pact_propose` for slot `promote_candidate` and effect type `git.sandbox_promote`. Its payload is `{repo_id, base_commit, files}`, where `files` maps allowlisted repository-relative paths to complete UTF-8 replacement contents. Proposals can come from Code Council's existing review/debate roles; this interface does not replace their reasoning workflow. After `pact_prepare` returns a digest and candidate evidence, an independent operator reviews and approves that digest; the initiating agent then requests commit. Inspect the actual sandbox ref and PACT receipt after the worker finishes.

Configure PACT with `PACT_CODE_SANDBOX_REPO`, `PACT_CODE_SANDBOX_REPO_ID`, and comma-separated `PACT_CODE_SANDBOX_ALLOWED_PATHS`. The repository must be a **disposable bare Git repository** with a dedicated `refs/heads/pact-sandbox` ref. The path is trusted server configuration, never an agent argument. The optional workflow is registered only when this configuration is present. Do not configure the user's working repository, a production remote, or a deploy/merge branch. The adapter never pushes or runs candidate-supplied shell commands. Its preparation checks are static fixture checks, so arbitrary test execution is **UNSUPPORTED** until a separate isolation boundary with no production secrets or network access exists.

The harness is `backend/tests/test_int_git_sandbox_harness.py`. It creates a real temporary bare repository, proves path restrictions, content/evidence binding, stale-base rejection, atomic compare-and-swap, lost-response reconciliation by observing the ref, and an authenticated PACT approval/worker promotion from a fresh runtime instance. Run with a disposable Postgres test DB and `--basetemp=.pytest_tmp` from `backend`. These are **HARNESS_VERIFIED** results; **REAL_PROJECT_NOT_RUN**. Code Council source access and an isolated test runner are still required before claiming an actual integration or protected-write credential isolation.
