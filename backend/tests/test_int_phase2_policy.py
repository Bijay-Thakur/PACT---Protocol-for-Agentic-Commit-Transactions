"""Trusted policy, delegation and digest-bound approval acceptance probes."""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.domain.capability import CapabilitySpec
from app.domain.effect import EffectProposal
from app.domain.enums import PrincipalKind
from app.domain.errors import AuthorityViolation, StateConflict
from app.domain.transaction import ApprovalRequest, BeginRequest, CommitRequest, DelegateRequest
from app.persistence.models import ApprovalRow, PlanRevisionRow
from app.policy.digest import digest
from app.security.principals import Forbidden

from .conftest import requires_db

pytestmark = requires_db


async def principals(rt, tenant: str):
    agent = await rt.principals.upsert_principal(
        tenant, "agent", PrincipalKind.AGENT,
        ["tx:begin", "tx:delegate", "tx:propose", "tx:prepare", "tx:commit"],
        {"workflows": {"customer_remediation": {"remediation_budget": "200.00",
                                                 "may_delegate_to": ["child"]}}})
    child = await rt.principals.upsert_principal(tenant, "child", PrincipalKind.AGENT,
        ["tx:propose", "tx:prepare"], {})
    approver = await rt.principals.upsert_principal(tenant, "finance", PrincipalKind.OPERATOR,
        ["op:approve"], {"roles": ["finance_approver"]})
    wrong_role = await rt.principals.upsert_principal(tenant, "viewer", PrincipalKind.OPERATOR,
        ["op:approve"], {"roles": []})
    return agent, child, approver, wrong_role


async def prepared_refund(rt, agent, cid: str, incident: str):
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    charge = (await rt.http.get(f"/sim/state/{cid}")).json()["billing"]["account"]["charges"][0]["id"]
    root = await rt.manager.begin(agent, BeginRequest(workflow="customer_remediation",
        business_request={"customer_id": cid, "incident_id": incident}))
    effect_id, _ = await rt.manager.propose(agent, root, EffectProposal(
        effect_type="billing.refund", slot="refund_line",
        payload={"customer_id": cid, "charge_id": charge, "amount": "50.00"}))
    frozen = await rt.coordinator.prepare(agent, root)
    assert frozen["status"] == "FROZEN", frozen
    return root, effect_id, charge, frozen


async def test_required_offboarding_actions_cannot_be_omitted(rt):
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    agent = await rt.principals.upsert_principal(tenant, "offboard", PrincipalKind.AGENT,
        ["tx:begin", "tx:prepare", "tx:commit"],
        {"workflows": {"customer_offboarding": {"amount_limit": "100.00"}}})
    root = await rt.manager.begin(agent, BeginRequest(workflow="customer_offboarding",
        business_request={"customer_id": "C-EMPTY"}))
    result = await rt.coordinator.prepare(agent, root)
    assert result["status"] == "REJECTED", result
    assert any(issue["code"] == "MISSING_REQUIRED_EFFECT" for issue in result["issues"])
    with pytest.raises(StateConflict):
        await rt.coordinator.request_commit(agent, root,
            CommitRequest(revision_digest="0" * 64))


async def test_delegation_cannot_expand_resource_amount_or_depth(rt):
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    agent, child, _, _ = await principals(rt, tenant)
    root = await rt.manager.begin(agent, BeginRequest(workflow="customer_remediation",
        business_request={"customer_id": "C-DELEGATE", "incident_id": "INC-DELEGATE"}))
    for spec in (
        CapabilitySpec(allowed_effect_types=["billing.refund"],
                       allowed_resources=["billing/customer:C-OTHER/*"],
                       amount_limit="100", cumulative_amount_limit="100"),
        CapabilitySpec(allowed_effect_types=["billing.refund"],
                       allowed_resources=["billing/customer:C-DELEGATE/*"],
                       amount_limit="201", cumulative_amount_limit="201"),
        CapabilitySpec(allowed_effect_types=["billing.refund"],
                       allowed_resources=["billing/customer:C-DELEGATE/*"],
                       amount_limit="100", cumulative_amount_limit="100", delegation_depth=2),
        CapabilitySpec(allowed_effect_types=["billing.refund"],
                       allowed_resources=["billing/customer:C-DELEGATE/*"],
                       amount_limit="100", cumulative_amount_limit="100",
                       expires_at=datetime.now(UTC) + timedelta(days=2)),
    ):
        with pytest.raises(AuthorityViolation):
            await rt.manager.delegate(agent, root, DelegateRequest(recipient=child.name,
                objective="narrow refund", capability=spec))


async def test_material_revision_and_expired_or_wrong_approvals_block(rt):
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    agent, _, approver, wrong_role = await principals(rt, tenant)
    cid = f"C-POL-{uuid.uuid4().hex[:6].upper()}"
    incident = f"INC-{uuid.uuid4().hex[:6].upper()}"
    root, effect_id, charge, frozen = await prepared_refund(rt, agent, cid, incident)
    with pytest.raises(Forbidden):
        await rt.coordinator.approve(agent, root, ApprovalRequest(
            revision_digest=frozen["digest"], reason="Agent self approval"))
    with pytest.raises(Forbidden):
        await rt.coordinator.approve(wrong_role, root, ApprovalRequest(
            revision_digest=frozen["digest"], reason="Wrong role"))
    with pytest.raises(Forbidden) as forged:
        await rt.coordinator.approve(approver, root, ApprovalRequest(
            revision_digest=frozen["digest"], reason="Forged operator",
            operator_id="another_operator"))
    assert forged.value.code == "FORGED_IDENTITY"
    with pytest.raises(StateConflict):
        await rt.coordinator.approve(approver, root, ApprovalRequest(
            revision_digest="0" * 64, reason="Wrong digest"))
    await rt.coordinator.approve(approver, root, ApprovalRequest(
        revision_digest=frozen["digest"], reason="Reviewed first revision"))
    await rt.coordinator.revise(agent, root, "Amount changed after review")
    await rt.manager.withdraw(agent, root, effect_id, "Replace amount")
    await rt.manager.propose(agent, root, EffectProposal(
        effect_type="billing.refund", slot="refund_line",
        payload={"customer_id": cid, "charge_id": charge, "amount": "60.00"}))
    revised = await rt.coordinator.prepare(agent, root)
    assert revised["status"] == "FROZEN", revised
    assert revised["digest"] != frozen["digest"]
    with pytest.raises(StateConflict):
        await rt.coordinator.request_commit(agent, root,
            CommitRequest(revision_digest=frozen["digest"]))
    awaiting = await rt.coordinator.request_commit(agent, root,
        CommitRequest(revision_digest=revised["digest"]))
    assert awaiting["status"] == "AWAITING_APPROVAL", awaiting
    await rt.coordinator.approve(approver, root, ApprovalRequest(
        revision_digest=revised["digest"], reason="Reviewed changed amount"))
    async with rt.db.uow() as session:
        rows = (await session.execute(select(ApprovalRow).where(
            ApprovalRow.root_id == root, ApprovalRow.digest == revised["digest"]))).scalars().all()
        assert rows
        for row in rows:
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    expired = await rt.coordinator.request_commit(agent, root,
        CommitRequest(revision_digest=revised["digest"]))
    assert expired["status"] == "AWAITING_APPROVAL", expired


async def test_frozen_digest_covers_effect_payload_dependencies_policy_and_contract(rt):
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    agent, _, approver, _ = await principals(rt, tenant)
    cid = f"C-DIGEST-{uuid.uuid4().hex[:6].upper()}"
    root, _, _, frozen = await prepared_refund(rt, agent, cid,
        f"INC-{uuid.uuid4().hex[:6].upper()}")
    async with rt.db.read() as session:
        revision = (await session.execute(select(PlanRevisionRow).where(
            PlanRevisionRow.root_id == root, PlanRevisionRow.digest == frozen["digest"]))).scalar_one()
        document = revision.compiled
    assert digest(document) == frozen["digest"]

    def changes():
        return {
            "effect removed": lambda d: d["effects"].clear(),
            "payload": lambda d: d["effects"][0]["payload"].update(amount="51.00"),
            "dependency": lambda d: d["effects"][0]["depends_on"].append("other-operation"),
            "resource": lambda d: d["effects"][0]["claims"][0].update(resource="billing/customer:OTHER"),
            "policy version": lambda d: d["workflow"].update(policy_version="changed"),
            "contract version": lambda d: d["effects"][0]["contract"].update(version="changed"),
            "authority": lambda d: d["authority"][0].update(amount_limit="1.00"),
        }

    for name, alter in changes().items():
        changed = deepcopy(document)
        alter(changed)
        assert digest(changed) != frozen["digest"], name
    await rt.coordinator.approve(approver, root, ApprovalRequest(
        revision_digest=frozen["digest"], reason="Reviewed exact frozen plan"))
    async with rt.db.read() as session:
        approvals = (await session.execute(select(ApprovalRow).where(
            ApprovalRow.root_id == root))).scalars().all()
        assert len(approvals) == 1 and approvals[0].digest == digest(document)
