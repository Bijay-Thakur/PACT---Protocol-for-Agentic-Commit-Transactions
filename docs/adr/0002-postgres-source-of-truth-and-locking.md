# ADR-0002: PostgreSQL as source of truth; aggregate lock + optimistic versions

**Status:** accepted

**Decision.**
- All transaction truth lives in PostgreSQL. The coordinator holds no state between units of work, and restart recovery reads only the database.
- Each mutating unit of work first takes `SELECT … FOR UPDATE` on the **root** row, which is the aggregate lock for the whole tree. Binding barrier evaluations also lock the tree's `logical_operations` rows in `operation_key` order, because logical operations are shared across roots.
- `version` columns (SQLAlchemy `version_id_col`) on transactions, effects and logical operations catch any write that bypasses the protocol (`CONCURRENCY_CONFLICT`).
- `UNIQUE(operation_key)` + `INSERT … ON CONFLICT DO NOTHING` arbitrates concurrent proposals of the same logical operation.
- A state change and its event are committed atomically. `transaction_events` is append-only and `receipts` immutable, both enforced by `BEFORE UPDATE OR DELETE` triggers. `state` columns have CHECK constraints over the protocol enums.
- No DB locks are held during external calls. The persisted state (`COMMITTING`, plus `DISPATCHING` with an `INTENT_RECORDED` attempt) is what serializes work.

**Consequences.** Concurrent commits, concurrent child proposals and racing duplicate operations are safe and deterministic (`tests/test_int_concurrency.py`). Work on a single root is serialized, which is acceptable for business transactions. Advisory locks were not needed.
