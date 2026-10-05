"""Standalone or embedded durable worker for Phase 2 work items."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from uuid import UUID

from app.config import Settings
from app.runtime import Runtime

log = logging.getLogger("pact.worker")


class Worker:
    def __init__(self, rt: Runtime, *, worker_id: str | None = None):
        self.rt = rt
        self.worker_id = worker_id or f"{os.getpid()}-{uuid.uuid4().hex[:8]}"

    async def run_once(self, root_id: UUID | None = None) -> bool:
        claim = await self.rt.queue.claim(self.worker_id, root_id=root_id)
        if claim is None:
            return False
        stop = asyncio.Event()

        async def heartbeat() -> None:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=max(self.rt.queue.lease_s / 3, 0.25))
                except TimeoutError:
                    if not await self.rt.queue.heartbeat(claim):
                        return

        beat = asyncio.create_task(heartbeat())
        try:
            delay = await self.rt.coordinator.step(claim)
            await self.rt.queue.finish(claim, next_delay_s=delay)
        except Exception as exc:
            log.exception("work item %s failed", claim.item_id)
            await self.rt.queue.finish(claim, next_delay_s=1.0, error=type(exc).__name__)
        finally:
            stop.set()
            await beat
        return True

    async def run_forever(self) -> None:
        tick = 0
        while True:
            if not await self.run_once():
                await asyncio.sleep(0.25)
            tick += 1
            if tick % 20 == 0:
                await self.rt.coordinator.sweep()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    rt = Runtime(Settings())
    await rt.start()
    try:
        await Worker(rt).run_forever()
    finally:
        await rt.stop()


if __name__ == "__main__":
    asyncio.run(main())
