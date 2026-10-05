"""Durable Postgres work queue with leases and epoch fencing.

- One active (READY/CLAIMED) item per root, enforced by a partial unique index.
- ``claim`` uses ``FOR UPDATE SKIP LOCKED``; it takes READY items that are due, or
  CLAIMED items whose lease has *expired* (never unexpired work of a live worker).
- Every claim increments ``epoch``. A worker's state-changing unit of work first
  calls ``fence`` which re-reads the item under lock; if another worker has since
  taken over (epoch changed), the stale worker is fenced out (``StaleWorker``).
- Times use the database clock (``now()``) so leases are consistent across processes.

Fencing protects PACT's database, not an already-sent external request: a late
provider response from a fenced worker is recorded as non-authoritative evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import WorkKind, WorkStatus
from app.domain.errors import PactError
from app.persistence.db import Database


class StaleWorker(PactError):
    status_code = 409
    code = "STALE_WORKER"


@dataclass(frozen=True)
class Claim:
    item_id: UUID
    root_id: UUID
    kind: str
    epoch: int
    worker_id: str
    takeover: bool
    runs: int
    payload: dict[str, Any]


class WorkQueue:
    def __init__(self, db: Database, *, lease_s: float = 15.0, max_runs: int = 400, deadline_s: float = 900.0):
        self.db = db
        self.lease_s = lease_s
        self.max_runs = max_runs
        self.deadline_s = deadline_s

    async def enqueue(self, s: AsyncSession, tenant_id: str, root_id: UUID, kind: WorkKind = WorkKind.DRIVE,
                      *, delay_s: float = 0.0, reason: str = "") -> None:
        """Schedule work for a root in the caller's transaction (atomic with the state change)."""
        await s.execute(text("""
            INSERT INTO work_items (id, tenant_id, root_id, kind, status, due_at, epoch, runs, max_runs,
                                    deadline_at, payload, created_at, updated_at)
            VALUES (gen_random_uuid(), :tenant, :root, :kind, 'READY', now() + make_interval(secs => :delay), 0, 0,
                    :max_runs, now() + make_interval(secs => :deadline), jsonb_build_object('reason', CAST(:reason AS text)),
                    now(), now())
            ON CONFLICT (root_id) WHERE status IN ('READY', 'CLAIMED')
            DO UPDATE SET due_at = LEAST(work_items.due_at, EXCLUDED.due_at),
                          payload = work_items.payload || jsonb_build_object('wake', true, 'reason', CAST(:reason AS text)),
                          updated_at = now()
        """), {"tenant": tenant_id, "root": root_id, "kind": str(kind), "delay": delay_s, "max_runs": self.max_runs,
               "deadline": self.deadline_s, "reason": reason})

    async def claim(self, worker_id: str, *, root_id: UUID | None = None) -> Claim | None:
        async with self.db.uow() as s:
            row = (await s.execute(text("""
                WITH c AS (
                    SELECT id, status AS prev_status FROM work_items
                    WHERE ((status = 'READY' AND due_at <= now())
                           OR (status = 'CLAIMED' AND lease_expires_at < now()))
                      AND (CAST(:root AS uuid) IS NULL OR root_id = CAST(:root AS uuid))
                    ORDER BY due_at
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE work_items w SET status = 'CLAIMED', owner = :owner, epoch = w.epoch + 1,
                       lease_expires_at = now() + make_interval(secs => :lease), runs = w.runs + 1,
                       payload = w.payload - 'wake', updated_at = now()
                FROM c WHERE w.id = c.id
                RETURNING w.id, w.root_id, w.kind, w.epoch, w.runs, c.prev_status, w.payload
            """), {"owner": worker_id, "lease": self.lease_s, "root": root_id})).first()
        if row is None:
            return None
        return Claim(item_id=row.id, root_id=row.root_id, kind=row.kind, epoch=row.epoch, worker_id=worker_id,
                     takeover=row.prev_status == WorkStatus.CLAIMED, runs=row.runs, payload=dict(row.payload or {}))

    async def heartbeat(self, claim: Claim) -> bool:
        async with self.db.uow() as s:
            res = await s.execute(text("""
                UPDATE work_items SET lease_expires_at = now() + make_interval(secs => :lease), updated_at = now()
                WHERE id = :id AND epoch = :epoch AND status = 'CLAIMED'
            """), {"id": claim.item_id, "epoch": claim.epoch, "lease": self.lease_s})
            return res.rowcount == 1

    async def fence(self, s: AsyncSession, claim: Claim | None) -> None:
        """Assert (under row lock, in the caller's transaction) that this worker still owns the item."""
        if claim is None:
            return
        row = (await s.execute(text("SELECT epoch, status FROM work_items WHERE id = :id FOR UPDATE"),
                               {"id": claim.item_id})).first()
        if row is None or row.epoch != claim.epoch or row.status != WorkStatus.CLAIMED:
            raise StaleWorker(f"work item {claim.item_id} epoch {claim.epoch} was taken over (now {row and row.epoch})",
                              details={"epoch": claim.epoch, "current": row and row.epoch})

    async def finish(self, claim: Claim, *, next_delay_s: float | None, error: str | None = None) -> str:
        """Complete or reschedule. Guarded by epoch; returns the resulting status (or 'FENCED')."""
        async with self.db.uow() as s:
            row = (await s.execute(text(
                "SELECT epoch, status, runs, max_runs, deadline_at < now() AS overdue, payload FROM work_items "
                "WHERE id = :id FOR UPDATE"), {"id": claim.item_id})).first()
            if row is None or row.epoch != claim.epoch or row.status != WorkStatus.CLAIMED:
                return "FENCED"
            woken = bool((row.payload or {}).get("wake"))
            if next_delay_s is None and woken:
                next_delay_s = 0.0
            if next_delay_s is not None and (row.runs >= row.max_runs or row.overdue):
                status, next_delay_s = WorkStatus.FAILED, None
                error = error or ("recovery deadline exceeded" if row.overdue else "max runs exceeded")
            elif next_delay_s is None:
                status = WorkStatus.DONE
            else:
                status = WorkStatus.READY
            await s.execute(text("""
                UPDATE work_items SET status = :status, owner = NULL, lease_expires_at = NULL, last_error = :err,
                       due_at = CASE WHEN CAST(:delay AS double precision) IS NULL THEN due_at
                                     ELSE now() + make_interval(secs => CAST(:delay AS double precision)) END,
                       payload = payload - 'wake', updated_at = now()
                WHERE id = :id
            """), {"status": str(status), "err": error, "delay": next_delay_s, "id": claim.item_id})
            return str(status)

    async def active_item(self, s: AsyncSession, root_id: UUID) -> Any:
        return (await s.execute(text(
            "SELECT id, status, owner, epoch, lease_expires_at, due_at, runs, last_error FROM work_items "
            "WHERE root_id = :r AND status IN ('READY', 'CLAIMED')"), {"r": root_id})).first()

    async def expire_lease_for_tests(self, root_id: UUID) -> None:
        """Test helper: simulate the passage of the lease (a crashed worker never heartbeats)."""
        async with self.db.uow() as s:
            await s.execute(text("UPDATE work_items SET lease_expires_at = now() - interval '1 second' "
                                 "WHERE root_id = :r AND status = 'CLAIMED'"), {"r": root_id})
