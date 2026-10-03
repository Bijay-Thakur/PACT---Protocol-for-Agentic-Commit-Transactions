"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import demo, effects, events, planner, receipts, transactions
from app.config import Settings
from app.domain.errors import PactError
from app.runtime import Runtime
from app.services.query_service import QueryService
from app.telemetry import tracing

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
            resumed = await rt.coordinator.resume_inflight()
            if resumed:
                log.info("restart recovery resumed %d transaction(s)", len(resumed))
        yield
        if runtime is None:
            await rt.stop()

    app = FastAPI(title="PACT - Protocol for Agentic Commit Transactions", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"],
                       allow_headers=["*"])

    @app.exception_handler(PactError)
    async def pact_error(_: Request, exc: PactError) -> JSONResponse:
        return JSONResponse(jsonable_encoder({"error": exc.to_dict()}), status_code=exc.status_code)

    @app.get("/healthz")
    async def health() -> dict:
        return {"status": "ok"}

    for r in (transactions.router, events.router, receipts.router, effects.router, demo.router, planner.router):
        app.include_router(r)
    return app


app = create_app()
