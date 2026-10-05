"""Opt-in demo stays on the same policy and worker path."""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.domain.enums import PrincipalKind
from app.main import create_app
from app.runtime import Runtime

from .conftest import make_settings, requires_db

pytestmark = requires_db


@pytest.mark.parametrize("scenario", ["success", "invariant-failure", "budget-conflict", "unknown",
    "compensation", "compensation-failure", "notification-ordering", "verification-mismatch",
    "duplicate-operation"])
async def test_authenticated_multiactor_demo_success(migrated_db, scenario):
    settings = make_settings(demo_mode=True, embedded_worker=False, auto_recover_on_startup=False)
    rt = Runtime(settings)
    await rt.start()
    try:
        tenant = f"test-{uuid.uuid4().hex[:6]}"
        operator = await rt.principals.upsert_principal(
            tenant, "operator", PrincipalKind.OPERATOR, ["demo:run", "tx:read_all"], {})
        key = await rt.principals.issue_api_key(operator.id)
        app = create_app(settings, runtime=rt)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://pact",
                                         headers={"Authorization": f"Bearer {key}"}, timeout=60) as client:
                response = await client.post(f"/api/v1/demo/run/{scenario}", json={"background": False})
                assert response.status_code == 200, response.text
                run = response.json()
                assert run["state"] == run["expected_state"], run
                if scenario in {"invariant-failure", "budget-conflict"}:
                    calls = (await rt.http.get("/sim/calls", params={
                        "customer_id": run["customer_id"]})).json()
                    assert calls == [], calls
                detail = await client.get(f"/api/v1/transactions/{run['root_id']}")
                assert detail.status_code == 200, detail.text
                assert len(detail.json()["tree"]) >= 4
                assert len(detail.json()["effects"]) >= 3
    finally:
        await rt.stop()
