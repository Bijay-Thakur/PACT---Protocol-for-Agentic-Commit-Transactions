"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import auth, demo, effects, events, planner, receipts, transactions
from app.config import Settings
from app.domain.errors import PactError
from app.runtime import Runtime
from app.services.query_service import QueryService
from app.telemetry import tracing
from app.worker import Worker

log = logging.getLogger("pact")


def create_app(settings: Settings | None = None, runtime: Runtime | None = None) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(level=settings.log_level,
                        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
    logging.getLogger("httpx").setLevel(logging.WARNING)
    tracing.configure(settings.otel_exporter)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        rt = runtime or Runtime(settings)
        await rt.start()
        app.state.runtime = rt
        app.state.queries = QueryService(rt)
        if settings.auto_recover_on_startup:
            await rt.coordinator.sweep(stranded_grace_s=0)
        worker_task = asyncio.create_task(Worker(rt).run_forever()) if settings.embedded_worker else None
        try:
            yield
        finally:
            if worker_task is not None:
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass
            if runtime is None:
                await rt.stop()

    app = FastAPI(title="PACT - Protocol for Agentic Commit Transactions", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"],
                       allow_headers=["*"], allow_credentials=True)

    @app.exception_handler(PactError)
    async def pact_error(_: Request, exc: PactError) -> JSONResponse:
        return JSONResponse(jsonable_encoder({"error": exc.to_dict()}), status_code=exc.status_code)

    @app.get("/healthz")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready(request: Request) -> JSONResponse:
        try:
            rt = request.app.state.runtime
            async with rt.db.read() as session:
                await session.execute(text("SELECT 1"))
                version = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
            if version != "0003":
                return JSONResponse({"status": "not_ready", "reason": "migration_outdated"}, status_code=503)
            return JSONResponse({"status": "ready", "migration": version,
                                 "model_profile": rt.planner.name,
                                 "model_live_checked": False})
        except Exception:
            return JSONResponse({"status": "not_ready", "reason": "database_unavailable"}, status_code=503)

    for r in (auth.router, transactions.router, events.router, receipts.router, effects.router,
              planner.router, demo.router):
        app.include_router(r)
    return app


app = create_app()
