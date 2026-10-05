"""Authenticated API denies forged identity and cross-principal reads."""

from __future__ import annotations

import uuid

import httpx

from app.domain.effect import EffectProposal
from app.domain.enums import PrincipalKind
from app.domain.transaction import BeginRequest
from app.main import create_app

from .conftest import make_settings, requires_db

pytestmark = requires_db


async def test_api_requires_identity_and_scopes_reads(rt):
    tenant = f"test-{uuid.uuid4().hex[:6]}"
    owner = await rt.principals.upsert_principal(
        tenant, "owner", PrincipalKind.AGENT, ["tx:begin", "tx:propose"],
        {"workflows": {"customer_remediation": {"remediation_budget": "100.00"}}})
    other = await rt.principals.upsert_principal(tenant, "other", PrincipalKind.AGENT, ["tx:begin"], {})
    key = await rt.principals.issue_api_key(owner.id)
    other_key = await rt.principals.issue_api_key(other.id)
    app = create_app(make_settings(embedded_worker=False), runtime=rt)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://pact") as client:
            url = "/api/v1/transactions"
            body = {"workflow": "customer_remediation", "business_request": {
                "customer_id": "C-AUTH", "incident_id": "INC-AUTH"}}
            assert (await client.post(url, json=body)).status_code == 401
            forged = await client.post(url, headers={"Authorization": f"Bearer {key}"},
                                       json={**body, "actor_id": "other"})
            assert forged.status_code == 403
            assert forged.json()["error"]["code"] == "FORGED_IDENTITY"
            for claim in ({"issuer": "grant:other"}, {"tenant_id": "another-tenant"}):
                rejected = await client.post(url, headers={"Authorization": f"Bearer {key}"},
                                             json={**body, **claim})
                assert rejected.status_code == 403
                assert rejected.json()["error"]["code"] == "FORGED_IDENTITY"
            created = await client.post(url, headers={"Authorization": f"Bearer {key}"}, json=body)
            assert created.status_code == 201, created.text
            rid = created.json()["transaction_id"]
            denied = await client.get(f"{url}/{rid}", headers={"Authorization": f"Bearer {other_key}"})
            assert denied.status_code == 404, denied.text
            for suffix in ("/events", "/receipt", "/receipt/verify", "/commit-decision"):
                scoped = await client.get(f"{url}/{rid}{suffix}",
                    headers={"Authorization": f"Bearer {other_key}"})
                assert scoped.status_code == 404, (suffix, scoped.text)
            visible = await client.get(f"{url}/{rid}", headers={"Authorization": f"Bearer {key}"})
            assert visible.status_code == 200, visible.text
            effect_url = f"{url}/{rid}/effects"
            headers = {"Authorization": f"Bearer {key}", "X-PACT-Request-ID": "proposal-1"}
            proposal = {"effect_type": "billing.refund", "slot": "refund_line",
                        "payload": {"customer_id": "C-AUTH", "amount": "10.00"}}
            first = await client.post(effect_url, headers=headers, json=proposal)
            replay = await client.post(effect_url, headers=headers, json=proposal)
            assert first.status_code == replay.status_code == 201
            assert first.json()["effect_id"] == replay.json()["effect_id"]
            conflict = await client.post(effect_url, headers=headers,
                json={**proposal, "payload": {"customer_id": "C-AUTH", "amount": "20.00"}})
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "REQUEST_ID_CONFLICT"


async def test_console_session_and_api_key_use_one_approval_policy(rt):
    """The console's cookie/CSRF path and agent's bearer path meet at the same frozen plan."""
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    cid = f"C-PARITY-{uuid.uuid4().hex[:8].upper()}"
    agent = await rt.principals.upsert_principal(tenant, "agent", PrincipalKind.AGENT,
        ["tx:begin", "tx:propose", "tx:prepare", "tx:commit"],
        {"workflows": {"customer_remediation": {"remediation_budget": "100.00"}}})
    operator = await rt.principals.upsert_principal(tenant, "finance", PrincipalKind.OPERATOR,
        ["op:approve", "tx:read_all"], {"roles": ["finance_approver"]})
    await rt.principals.set_password(operator.id, "finance", "test-password")
    agent_key = await rt.principals.issue_api_key(agent.id)
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    charge = (await rt.http.get(f"/sim/state/{cid}")).json()["billing"]["account"]["charges"][0]["id"]
    root = await rt.manager.begin(agent, BeginRequest(workflow="customer_remediation",
        business_request={"customer_id": cid, "incident_id": f"INC-{uuid.uuid4().hex[:8]}"}))
    await rt.manager.propose(agent, root, EffectProposal(effect_type="billing.refund",
        slot="refund_line", payload={"customer_id": cid, "charge_id": charge, "amount": "50.00"}))
    frozen = await rt.coordinator.prepare(agent, root)
    assert frozen["status"] == "FROZEN", frozen

    app = create_app(make_settings(embedded_worker=False), runtime=rt)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://pact") as browser:
            login = await browser.post("/api/v1/auth/login", json={
                "tenant_id": tenant, "username": "finance", "password": "test-password"})
            assert login.status_code == 200, login.text
            csrf = login.json()["csrf_token"]
            assert browser.cookies.get("pact_session")
            detail = await browser.get(f"/api/v1/transactions/{root}")
            assert detail.status_code == 200, detail.text
            assert detail.json()["plan_revision"]["digest"] == frozen["digest"]
            approve_url = f"/api/v1/transactions/{root}/approve"
            body = {"revision_digest": frozen["digest"], "reason": "Reviewed frozen refund"}
            no_csrf = await browser.post(approve_url, json=body)
            assert no_csrf.status_code == 403 and no_csrf.json()["error"]["code"] == "CSRF_REJECTED"
            wrong = await browser.post(approve_url, headers={"X-PACT-CSRF": csrf},
                json={**body, "revision_digest": "0" * 64})
            assert wrong.status_code == 409, wrong.text
            mixed = await browser.post(approve_url,
                headers={"Authorization": f"Bearer {agent_key}"}, json=body)
            assert mixed.status_code == 401 and mixed.json()["error"]["code"] == "UNAUTHENTICATED"
            approved = await browser.post(approve_url, headers={"X-PACT-CSRF": csrf}, json=body)
            assert approved.status_code == 200, approved.text
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://pact",
                                     headers={"Authorization": f"Bearer {agent_key}"}) as agent_client:
            agent_approval = await agent_client.post(approve_url, json=body)
            assert agent_approval.status_code == 403, agent_approval.text
            queued = await agent_client.post(f"/api/v1/transactions/{root}/commit",
                json={"revision_digest": frozen["digest"]})
            assert queued.status_code == 200 and queued.json()["status"] == "QUEUED", queued.text
