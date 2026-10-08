# Next-phase baseline

Captured 7 October 2026 before implementation changes.

## Source

- Branch: `main`
- Local SHA: `7142a93c0a37790beb89cca42b10e6d7bec81375`
- `origin/main`: `7142a93c0a37790beb89cca42b10e6d7bec81375`, verified with `git ls-remote`
- Initial status: clean except for the two user-supplied, untracked next-phase specification files
- Evidence type: local source and environment inspection; remote branch identity only

## Environment and checks

- OS: Windows 10 build 26300
- Shell: PowerShell
- Default Python: 3.14.7
- Existing `.venv`: Python 3.13 packages
- Docker: `BLOCKED` — the Docker Desktop Linux engine named pipe was absent
- Unit baseline: `BLOCKED` before collection — Windows Application Control rejected
  SQLAlchemy's `_processors_cy` extension in the existing `.venv`. Retrying with
  `DISABLE_SQLALCHEMY_CEXT_RUNTIME=1` produced the same denial.
- Disposable PostgreSQL integration baseline: `NOT_RUN`; no destructive command
  was issued and no working database was touched.

The prior 90-unit-test, 178-backend-test, browser, migration, MCP, and live-model
counts are historical evidence only. They are not relabeled as results from this
implementation run. Generated reports belong under ignored `.local/`; secrets,
raw private model traces, and customer data must not be committed.
