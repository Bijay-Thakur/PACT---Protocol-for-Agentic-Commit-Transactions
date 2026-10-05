"""Operator session endpoints. API keys are provisioned outside the browser."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import principal, runtime
from app.runtime import Runtime
from app.security.principals import Principal

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1)


@router.post("/login")
async def login(body: LoginRequest, response: Response, request: Request,
                rt: Runtime = Depends(runtime)) -> dict:
    p, session, csrf = await rt.principals.login(body.tenant_id, body.username, body.password)
    response.set_cookie("pact_session", session, httponly=True, secure=request.url.scheme == "https",
                        samesite="strict", max_age=8 * 3600, path="/")
    return {"principal": {"name": p.name, "tenant_id": p.tenant_id, "roles": p.roles}, "csrf_token": csrf}


@router.get("/me")
async def me(p: Principal = Depends(principal)) -> dict:
    return {"name": p.name, "tenant_id": p.tenant_id, "kind": p.kind, "roles": p.roles,
            "scopes": sorted(p.scopes)}


@router.post("/logout")
async def logout(response: Response, request: Request, p: Principal = Depends(principal),
                 rt: Runtime = Depends(runtime)) -> dict:
    session = request.cookies.get("pact_session")
    if session:
        await rt.principals.logout(session)
    response.delete_cookie("pact_session", path="/")
    return {"logged_out": True}
