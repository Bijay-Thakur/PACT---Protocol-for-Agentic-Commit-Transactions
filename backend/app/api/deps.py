from __future__ import annotations

from fastapi import Request

from app.runtime import Runtime
from app.services.query_service import QueryService


def runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def queries(request: Request) -> QueryService:
    return request.app.state.queries
