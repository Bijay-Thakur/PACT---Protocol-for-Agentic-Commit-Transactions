# Phase 2 local runbook

Phase 2 is under implementation. All protected calls use registered simulated providers in the verified local flows. The old Phase 1 request schema and unauthenticated scripts are no longer the supported API. The [checklist](implementation-checklist.md) records incomplete gates.

## Start on Windows PowerShell

The quickest verified path uses the repository `.env`, an existing development database named by `PACT_DATABASE_URL`, the Python virtual environment, and installed frontend packages:

```powershell
cd D:\Projects\PACT
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_local.ps1 -SetupOperator
# Enter a private password at the prompt; then open http://localhost:3000.
```

The launcher migrates the configured database, enables local simulator demos, starts an embedded-worker API and the Next.js console, and checks both health endpoints. Sign in as tenant `local`, username `operator`. For later starts, omit `-SetupOperator`. Stop both processes with `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_local.ps1`. Logs are in ignored `.local/` files. On this checkout, the owner’s `.env` uses Groq through `GROQ_API_KEY`; the key is never copied to the frontend.

If `.venv` or `frontend/node_modules` is missing, run `py -3.13 -m venv .venv`, `& .\.venv\Scripts\python.exe -m pip install -e 'backend[dev]'`, and `npm.cmd ci` from `frontend`. PostgreSQL must already have the development database. `.env` is **not automatically loaded** by direct Python commands; the manual commands below need environment variables set in their shell. Never point `PACT_TEST_DATABASE_URL` at a working database.

### Manual start

```powershell
cd D:\Projects\PACT
& .\.venv\Scripts\python.exe -m pip install -e backend
$env:PACT_DATABASE_URL = 'postgresql+asyncpg://USER:PASSWORD@localhost:5432/pact'
cd backend
..\.venv\Scripts\python.exe -m app.cli migrate
..\.venv\Scripts\python.exe -m app.cli create-operator local operator
..\.venv\Scripts\python.exe -m app.cli create-agent local root_agent ..\docs\phase2\agent-grants.example.json
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`create-agent` prints the new API key once. Store it privately. `create-operator` prompts for a password and stores only its verifier. The example grant is a template; adjust permitted recipients and amounts for the local simulation. The first endpoint to check is `/healthz`. API endpoints require a bearer agent key or an operator session.

The API process runs an embedded durable worker by default. To run it separately, set `PACT_EMBEDDED_WORKER=false` for the API process, then start `..\.venv\Scripts\python.exe -m app.worker` in another shell with the same database settings. Work items, leases, attempts and receipts live in Postgres; worker restarts resume from that state.

```powershell
cd D:\Projects\PACT\frontend
npm.cmd install
npm.cmd run dev
```

The console at `http://localhost:3000` asks for the operator tenant, username and password. Keep both the console and API on `localhost` so the Strict session cookie works across their ports. Mutations send the returned CSRF token. No administrator API key belongs in `NEXT_PUBLIC_*`.

## Simulator demos

Set `PACT_DEMO_MODE=true` before starting the backend to enable authenticated demo routes. A signed-in operator with `demo:run` may launch scenarios from the console. The same scenarios can run locally without HTTP:

```powershell
cd D:\Projects\PACT\backend
..\.venv\Scripts\python.exe -m app.cli scenario success unknown compensation
```

The CLI uses the same transaction manager, compiler, barrier and durable worker as REST. Its principals and providers are explicitly simulated. A separate crash test is `python -m app.cli crash-midflight`, followed after the worker lease expires by `python -m app.cli recover`. The crash command terminates its own process deliberately. Do not run it against the owner's working database while experimenting.

## MCP

Install `mcp>=2.3,<3` in the backend environment. Start the API, provision **one agent key for each MCP server process**, and set `PACT_MCP_API_KEY` only in that process's private environment. Then run:

```powershell
cd D:\Projects\PACT\backend
$env:PACT_API_URL = 'http://127.0.0.1:8000'
$env:PACT_MCP_API_KEY = '<private agent key>'
..\.venv\Scripts\python.exe -m app.mcp_server
```

The stdio server implements MCP initialize, discovery and tool calls with the official Python SDK. It forwards requests to authenticated REST services; MCP tool arguments cannot select a different principal. Do not type an API key into a chat or tool argument. The independent protocol test is `pytest -q -p no:cacheprovider tests/test_int_phase2_mcp.py` on a disposable test database.

The [integration guide](mcp-integration.md) has the pinned Node client example and the optional disposable Git sandbox workflow. The Git fixture's static checks do not execute untrusted code.

## Verification and limits

Use a database that passes `tests/db_guard.py` and set `PACT_TEST_DATABASE_URL` explicitly before integration tests. The fixture drops only that database's `public` schema. `python -m pytest -q -p no:cacheprovider --basetemp=.pytest_tmp backend/tests` exercised the full current suite on real Postgres (149 passed on 2026-10-04). The original Phase 1 integration sources are preserved under `backend/tests/legacy_int_*.py` because their anonymous API contract was replaced; see the [test transition](phase1-test-transition.md) for the coverage mapping.

Set `PACT_PLANNER_PROVIDER=nebius_nemotron` with `PACT_PLANNER_BASE_URL`, `PACT_PLANNER_MODEL`, and `PACT_PLANNER_API_KEY` for live inference. The deterministic default is labeled as a fixture. No live Nebius inference has been verified yet. Model output is a restricted proposal, never an authority grant or commit decision.

For live Groq inference, set `PACT_PLANNER_PROVIDER=groq`, `PACT_PLANNER_BASE_URL=https://api.groq.com/openai/v1`, `PACT_PLANNER_MODEL=openai/gpt-oss-20b`, and `GROQ_API_KEY` in private `.env`. The owner has enabled `PACT_PLANNER_SHARE_WORKFLOW_CATALOG=true`, which sends registered workflow descriptions, field names, and action slots to Groq to ground its proposals. A live Groq CLI smoke returned `customer_offboarding` and `applied=false`; this does not satisfy the separate Nebius/NVIDIA acceptance check.

The console's **Interpret an intent** panel calls the same `/api/v1/planner/propose` and `/review` pipeline. It shows a read-only business request and required slots; an authorized agent still needs to begin, propose typed effects, prepare, and request commit. The model provider can also be checked without the database or UI:

```powershell
cd D:\Projects\PACT\backend
..\.venv\Scripts\python.exe -m app.cli planner-smoke 'Cancel customer C-123'
```

Existing operator accounts created before this update may need their scopes refreshed with `create-operator` to use the intent panel. The versioned 50-intent evaluation corpus is `backend/tests/fixtures/model_intents_v1.jsonl`; its deterministic fixture checks do not substitute for a live model evaluation.
