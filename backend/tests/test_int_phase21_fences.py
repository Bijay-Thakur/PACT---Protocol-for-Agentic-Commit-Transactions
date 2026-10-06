"""Database-backed authorization and overlapping preparation regressions."""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.agents.client import HttpPactClient, PactApiError
from app.domain.effect import EffectProposal
from app.domain.capability import CapabilitySpec
from app.domain.enums import PrincipalKind
from app.domain.transaction import ApprovalRequest, BeginRequest, CommitRequest, DelegateRequest
from app.domain.errors import StateConflict
from app.core.executor import AuthorityLost
from app.persistence.models import TransactionRow, EffectRow, LogicalOperationRow, OperationAttemptRow

from .conftest import requires_db

pytestmark = requires_db


async def _refund_draft(rt, suffix: str):
    tenant = f"phase21-{uuid.uuid4().hex[:8]}"
    cid = f"C-RACE-{uuid.uuid4().hex[:6].upper()}"
    incident = f"INC-{uuid.uuid4().hex[:6].upper()}"
    requester = await rt.principals.upsert_principal(tenant, "requester", PrincipalKind.AGENT,
        ["tx:begin", "tx:propose", "tx:prepare", "tx:commit"],
        {"workflows": {"customer_remediation": {"remediation_budget": "200.00"}}})
    approver = await rt.principals.upsert_principal(tenant, "approver", PrincipalKind.OPERATOR,
        ["op:approve"], {"roles": ["finance_approver"]})
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    charge = (await rt.http.get(f"/sim/state/{cid}")).json()["billing"]["account"]["charges"][0]["id"]
    root = await rt.manager.begin(requester, BeginRequest(workflow="customer_remediation",
        business_request={"customer_id": cid, "incident_id": incident}))
    effect_id, _ = await rt.manager.propose(requester, root, EffectProposal(
        effect_type="billing.refund", slot="refund_line",
        payload={"customer_id": cid, "charge_id": charge, "amount": "40.00"}))
    return requester, approver, root, effect_id, cid, charge


async def test_approval_role_removal_and_readd_needs_new_approval(rt):
    requester, approver, root, _, _, _ = await _refund_draft(rt, "role")
    frozen = await rt.coordinator.prepare(requester, root)
    assert frozen["status"] == "FROZEN", frozen
    await rt.coordinator.approve(approver, root, ApprovalRequest(
        revision_digest=frozen["digest"], reason="Reviewed request"))
    same = await rt.principals.upsert_principal(approver.tenant_id, approver.name, PrincipalKind.OPERATOR,
        ["op:approve"], {"roles": ["finance_approver"]})
    assert same.authorization_epoch == approver.authorization_epoch
    removed = await rt.principals.upsert_principal(approver.tenant_id, approver.name, PrincipalKind.OPERATOR,
        ["op:approve"], {"roles": []})
    assert removed.authorization_epoch > same.authorization_epoch
    blocked = await rt.coordinator.request_commit(requester, root, CommitRequest(revision_digest=frozen["digest"]))
    assert blocked["status"] == "AWAITING_APPROVAL", blocked
    restored = await rt.principals.upsert_principal(approver.tenant_id, approver.name, PrincipalKind.OPERATOR,
        ["op:approve"], {"roles": ["finance_approver"]})
    blocked_again = await rt.coordinator.request_commit(requester, root,
        CommitRequest(revision_digest=frozen["digest"]))
    assert blocked_again["status"] == "AWAITING_APPROVAL", blocked_again
    await rt.coordinator.approve(restored, root, ApprovalRequest(
        revision_digest=frozen["digest"], reason="New approval after authority changed"))
    queued = await rt.coordinator.request_commit(requester, root, CommitRequest(revision_digest=frozen["digest"]))
    assert queued["status"] == "QUEUED", queued


@pytest.mark.parametrize("old_arrives_first,restart_for_b", [(True, False), (False, False),
                                                                (False, True)])
async def test_prepare_generation_discards_old_read_after_reset_and_replacement(rt, monkeypatch,
                                                                                old_arrives_first,
                                                                                restart_for_b):
    requester, _, root, old_effect, cid, charge = await _refund_draft(rt, "prepare")
    adapter = rt.registry.adapter("billing.refund")
    original = adapter.prepare
    entered = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    count = 0

    async def paused_prepare(view, context):
        nonlocal count
        index = count
        count += 1
        entered[index].set()
        await release[index].wait()
        return await original(view, context)

    monkeypatch.setattr(adapter, "prepare", paused_prepare)
    a = asyncio.create_task(rt.coordinator.prepare(requester, root))
    await asyncio.wait_for(entered[0].wait(), 5)
    async with rt.db.uow() as session:
        row = await session.get(TransactionRow, root)
        row.updated_at = datetime.now(UTC) - timedelta(hours=1)
    swept = await rt.coordinator.sweep(prepare_stall_s=0)
    assert str(root) in swept["prepare_reset"]
    await rt.manager.withdraw(requester, root, old_effect, "replace stale draft")
    new_effect, _ = await rt.manager.propose(requester, root, EffectProposal(
        effect_type="billing.refund", slot="refund_line",
        payload={"customer_id": cid, "charge_id": charge, "amount": "50.00"}))
    b_runtime = rt
    if restart_for_b:
        from app.runtime import Runtime
        b_runtime = Runtime(rt.settings)
        await b_runtime.start()
        monkeypatch.setattr(b_runtime.registry.adapter("billing.refund"), "prepare", paused_prepare)
    b = asyncio.create_task(b_runtime.coordinator.prepare(requester, root))
    await asyncio.wait_for(entered[1].wait(), 5)
    if old_arrives_first:
        release[0].set()
        await asyncio.wait_for(a, 5)
        release[1].set()
        result = await asyncio.wait_for(b, 5)
    else:
        release[1].set()
        result = await asyncio.wait_for(b, 5)
        release[0].set()
        await asyncio.wait_for(a, 5)
    assert result["status"] == "FROZEN", result
    async with rt.db.read() as session:
        from app.persistence.models import EffectRow
        old = await session.get(EffectRow, old_effect)
        new = await session.get(EffectRow, new_effect)
        assert old.state == "ABORTED" and not old.prepare_evidence
        assert new.state == "PREPARED" and new.prepare_evidence["observations"]
    if restart_for_b:
        await b_runtime.stop()


async def test_begin_request_id_replays_once_and_conflicts_on_changed_body(rt):
    tenant = f"phase21-{uuid.uuid4().hex[:8]}"
    requester = await rt.principals.upsert_principal(tenant, "requester", PrincipalKind.AGENT,
        ["tx:begin"], {"workflows": {"customer_remediation": {"remediation_budget": "100"}}})
    body = BeginRequest(workflow="customer_remediation",
                        business_request={"customer_id": "C-REPLAY", "incident_id": "INC-REPLAY"})
    req_id = uuid.uuid4().hex
    first, second = await asyncio.gather(rt.manager.begin(requester, body, request_id=req_id),
                                         rt.manager.begin(requester, body, request_id=req_id))
    assert first == second
    with pytest.raises(StateConflict) as error:
        await rt.manager.begin(requester, BeginRequest(workflow="customer_remediation",
            business_request={"customer_id": "C-OTHER", "incident_id": "INC-REPLAY"}), request_id=req_id)
    assert error.value.code == "REQUEST_ID_CONFLICT"
    other = await rt.principals.upsert_principal(tenant, "other", PrincipalKind.AGENT,
        ["tx:begin"], {"workflows": {"customer_remediation": {"remediation_budget": "100"}}})
    assert await rt.manager.begin(other, body, request_id=req_id) != first


async def test_delegate_request_id_replays_once_and_conflicts_on_target(rt, client):
    tenant = f"phase21-{uuid.uuid4().hex[:8]}"
    requester = await rt.principals.upsert_principal(tenant, "requester", PrincipalKind.AGENT,
        ["tx:begin", "tx:delegate"], {"workflows": {"customer_remediation": {
            "remediation_budget": "200", "may_delegate_to": ["child"]}}})
    child = await rt.principals.upsert_principal(tenant, "child", PrincipalKind.AGENT, ["tx:propose"], {})
    root = await rt.manager.begin(requester, BeginRequest(workflow="customer_remediation",
        business_request={"customer_id": "C-DELEGATE-21", "incident_id": "INC-DELEGATE-21"}))
    body = DelegateRequest(recipient="child", objective="narrow refund",
        capability=CapabilitySpec(allowed_effect_types=["billing.refund"],
            allowed_resources=["billing/customer:C-DELEGATE-21/*"],
            amount_limit="100", cumulative_amount_limit="100"))
    req_id = uuid.uuid4().hex
    first, second = await asyncio.gather(rt.manager.delegate(requester, root, body, request_id=req_id),
                                         rt.manager.delegate(requester, root, body, request_id=req_id))
    assert first == second
    child_headers = {"Authorization": f"Bearer {await rt.principals.issue_api_key(child.id)}"}
    root_headers = {"Authorization": f"Bearer {await rt.principals.issue_api_key(requester.id)}"}
    bare = await client.get("/api/v1/workflows", headers=child_headers)
    assert bare.status_code == 200 and bare.json()["workflows"] == []
    scoped = await client.get("/api/v1/workflows", params={"transaction_id": str(first)},
                              headers=child_headers)
    assert scoped.status_code == 200
    assert [wf["key"] for wf in scoped.json()["workflows"]] == ["customer_remediation"]
    assert scoped.json()["workflows"][0]["may_begin"] is False
    contracts = await client.get("/api/v1/contracts", params={"transaction_id": str(first)},
                                 headers=child_headers)
    assert contracts.status_code == 200
    assert {contract["effect_type"] for contract in contracts.json()["contracts"]} == {"billing.refund"}
    hidden = await client.get("/api/v1/workflows", params={"transaction_id": str(first)},
                              headers=root_headers)
    assert hidden.status_code == 404
    with pytest.raises(StateConflict) as error:
        await rt.manager.delegate(requester, root, body.model_copy(update={"objective": "changed"}),
                                  request_id=req_id)
    assert error.value.code == "REQUEST_ID_CONFLICT"
    second_root = await rt.manager.begin(requester, BeginRequest(workflow="customer_remediation",
        business_request={"customer_id": "C-DELEGATE-22", "incident_id": "INC-DELEGATE-22"}))
    with pytest.raises(StateConflict) as error:
        await rt.manager.delegate(requester, second_root, body, request_id=req_id)
    assert error.value.code == "REQUEST_ID_CONFLICT"
    with pytest.raises(StateConflict) as error:
        await rt.manager.begin(requester, BeginRequest(workflow="customer_remediation",
            business_request={"customer_id": "C-DELEGATE-21", "incident_id": "INC-DELEGATE-21"}),
            request_id=req_id)
    assert error.value.code == "REQUEST_ID_CONFLICT"


async def test_python_client_repair_then_closed_execution_cannot_be_edited(rt, client):
    requester, approver, root, old_effect, cid, charge = await _refund_draft(rt, "repair")
    sdk = HttpPactClient(client, api_key=await rt.principals.issue_api_key(requester.id))
    old = await sdk.prepare(str(root))
    assert old["status"] == "FROZEN"
    reopened = await sdk.revise(str(root), "Fix draft amount")
    assert reopened["state"] == "SPECIFYING"
    assert (await sdk.withdraw(str(root), str(old_effect), "Replace draft refund"))["state"] == "ABORTED"
    proposal = await sdk.propose(str(root), "billing.refund", "refund_line",
                                 {"customer_id": cid, "charge_id": charge, "amount": "50.00"},
                                 request_id=uuid.uuid4().hex)
    assert proposal["effect_id"] != str(old_effect)
    fresh = await sdk.prepare(str(root))
    assert fresh["status"] == "FROZEN" and fresh["digest"] != old["digest"]
    with pytest.raises(PactApiError) as error:
        await sdk.request_commit(str(root), old["digest"])
    assert error.value.code == "REVISION_DIGEST_MISMATCH"
    await rt.coordinator.approve(approver, root, ApprovalRequest(
        revision_digest=fresh["digest"], reason="Reviewed repaired draft"))
    assert (await sdk.request_commit(str(root), fresh["digest"]))["status"] == "QUEUED"
    with pytest.raises(PactApiError) as error:
        await sdk.revise(str(root), "Too late")
    assert error.value.code == "REVISION_NOT_ALLOWED"
    with pytest.raises(PactApiError):
        await sdk.withdraw(str(root), proposal["effect_id"], "Too late")


async def test_stale_textual_retry_permission_cannot_reach_provider(rt):
    requester, approver, root, effect_id, cid, _ = await _refund_draft(rt, "retry")
    frozen = await rt.coordinator.prepare(requester, root)
    await rt.coordinator.approve(approver, root, ApprovalRequest(
        revision_digest=frozen["digest"], reason="Reviewed refund"))
    assert (await rt.coordinator.request_commit(requester, root,
            CommitRequest(revision_digest=frozen["digest"])))["status"] == "QUEUED"
    async with rt.db.uow() as session:
        effect = await session.get(EffectRow, effect_id)
        logical = await session.get(LogicalOperationRow, effect.logical_operation_id)
        effect.state = "RETRYABLE"
        logical.status = "RETRYABLE"
        effect.reconciliation_result = {"retry_justification": "old dedup text",
                                        "negative_authoritative": False}
        old_time = datetime.now(UTC) - timedelta(days=2)
        session.add(OperationAttemptRow(logical_operation_id=logical.id, effect_id=effect.id,
            kind="EXECUTE", attempt_no=1, status="RESPONSE_LOST", started_at=old_time,
            request={"idempotency_key": logical.provider_idempotency_key, "payload": effect.payload,
                     "contract_version": effect.contract_version}))
    before = (await rt.http.get("/sim/calls", params={"customer_id": cid,
                                                      "operation": "create_refund"})).json()
    assert await rt.coordinator.executor.dispatch(root, effect_id, None) is None
    async with rt.db.read() as session:
        held = await session.get(EffectRow, effect_id)
        assert held.state == "UNKNOWN"
    after = (await rt.http.get("/sim/calls", params={"customer_id": cid,
                                                     "operation": "create_refund"})).json()
    assert after == before == []


@pytest.mark.parametrize("revocation_point", ["before_intent", "after_intent"])
async def test_revocation_linearizes_at_durable_dispatch_intent(rt, monkeypatch, revocation_point):
    requester, approver, root, effect_id, cid, _ = await _refund_draft(rt, "revoke")
    frozen = await rt.coordinator.prepare(requester, root)
    await rt.coordinator.approve(approver, root, ApprovalRequest(
        revision_digest=frozen["digest"], reason="Reviewed before dispatch"))
    assert (await rt.coordinator.request_commit(requester, root,
        CommitRequest(revision_digest=frozen["digest"])))["status"] == "QUEUED"

    async def revoke(point, _effect_id):
        if point == revocation_point:
            await rt.principals.upsert_principal(approver.tenant_id, approver.name, PrincipalKind.OPERATOR,
                                                 ["op:approve"], {"roles": []})

    monkeypatch.setattr(rt.exec_ctx, "crash_hook", revoke)
    if revocation_point == "before_intent":
        with pytest.raises(AuthorityLost):
            await rt.coordinator.executor.dispatch(root, effect_id, None)
    else:
        outcome = await rt.coordinator.executor.dispatch(root, effect_id, None)
        assert outcome is not None and outcome.value == "ACCEPTED"
    calls = (await rt.http.get("/sim/calls", params={"customer_id": cid,
                                                    "operation": "create_refund"})).json()
    assert len(calls) == (0 if revocation_point == "before_intent" else 1)
    async with rt.db.read() as session:
        effect = await session.get(EffectRow, effect_id)
        assert effect.state == ("PREPARED" if revocation_point == "before_intent" else "DISPATCHED")
