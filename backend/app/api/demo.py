"""Demo routes: reproducible scenarios + read-only view of simulated external state."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.agents.client import HttpPactClient
from app.api.deps import runtime
from app.demo.scenarios import SCENARIOS, ScenarioOptions, run_scenario
from app.runtime import Runtime

router = APIRouter(prefix="/api/v1/demo", tags=["demo"])


class RunRequest(BaseModel):
    background: bool = False
    step_delay_ms: int = Field(default=0, ge=0, le=3000)
    pause_at_unknown: bool = False


@router.get("/scenarios")
async def scenarios() -> dict[str, Any]:
    return {"scenarios": [{"key": s.key, "title": s.title, "description": s.description,
                           "expected_state": s.expected_state, "faults": s.faults} for s in SCENARIOS.values()]}


@router.post("/run/{scenario}")
async def run(scenario: str, request: Request, body: RunRequest | None = None,
              rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    if scenario not in SCENARIOS:
        raise HTTPException(404, f"unknown scenario {scenario!r}; see /api/v1/demo/scenarios")
    body = body or RunRequest()
    # Agents use the public REST API (in-process transport), exactly like external agents would.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=request.app), base_url="http://pact",
                                 timeout=120) as http:
        return await run_scenario(scenario, HttpPactClient(http), rt.http,
                                  ScenarioOptions(background=body.background, step_delay_ms=body.step_delay_ms,
                                                  pause_at_unknown=body.pause_at_unknown))


@router.get("/external-state/{customer_id}")
async def external_state(customer_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Ground truth inside the simulated providers (for the console's 'external reality' panel)."""
    state = (await rt.http.get(f"/sim/state/{customer_id}")).json()
    calls = (await rt.http.get("/sim/calls", params={"customer_id": customer_id})).json()
    return {"state": state, "provider_calls": calls}
