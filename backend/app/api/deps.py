from __future__ import annotations

from fastapi import Depends, Request

from app.runtime import Runtime
from app.security.principals import AuthenticationFailed, Principal
from app.services.query_service import QueryService


def runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def queries(request: Request) -> QueryService:
    return request.app.state.queries


async def principal(request: Request, rt: Runtime = Depends(runtime)) -> Principal:
    bearer = request.headers.get("authorization", "")
    cookie = request.cookies.get("pact_session")
    if bearer and cookie:
        raise AuthenticationFailed("use one authentication method")
    if bearer:
        scheme, _, token = bearer.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise AuthenticationFailed("Bearer API key required")
        return await rt.principals.authenticate_api_key(token)
    if cookie:
        return await rt.principals.authenticate_session(
            cookie, request.headers.get("x-pact-csrf"),
            unsafe=request.method not in {"GET", "HEAD", "OPTIONS"})
    raise AuthenticationFailed("authentication required")
