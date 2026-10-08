"""Tenant-scoped operator queue queries use complete database aggregates."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.domain.enums import PrincipalKind
from app.domain.transaction import BeginRequest
from app.services.query_service import QueryService

from .conftest import requires_db

pytestmark = requires_db


async def test_empty_operator_queues_are_tenant_scoped_and_queryable(rt):
    tenant = f"next-phase-{uuid.uuid4().hex[:8]}"
    operator = await rt.principals.upsert_principal(
        tenant,
        "operator",
        PrincipalKind.OPERATOR,
        ["tx:read_all", "op:approve"],
        {"roles": ["refund_approver"]},
    )
    queries = QueryService(rt)
    overview = await queries.operations_overview(operator)
    assert overview["tenant_id"] == tenant
    assert overview["active"] == 0
    assert overview["pending_approvals"] == 0
    assert overview["incidents"] == 0
    assert overview["open_residuals"] == 0
    assert await queries.approval_queue(operator) == []
    assert await queries.incident_queue(operator) == []


async def test_transaction_filters_and_stable_cursor(rt):
    tenant = f"next-phase-{uuid.uuid4().hex[:8]}"
    requester = await rt.principals.upsert_principal(
        tenant,
        "requester",
        PrincipalKind.OPERATOR,
        ["tx:begin", "tx:read_all"],
        {"workflows": {"customer_offboarding": {"amount_limit": "500.00"}}},
    )
    older = await rt.manager.begin(
        requester,
        BeginRequest(
            workflow="customer_offboarding",
            business_request={"customer_id": "C-CURSOR-OLD"},
            objective="Older searchable request",
        ),
    )
    newer = await rt.manager.begin(
        requester,
        BeginRequest(
            workflow="customer_offboarding",
            business_request={"customer_id": "C-CURSOR-NEW"},
            objective="Newer searchable request",
        ),
    )
    queries = QueryService(rt)
    first = await queries.list_transactions(requester, 1, state="CREATED", search="searchable")
    assert [row["id"] for row in first] == [str(newer)]
    second = await queries.list_transactions(
        requester,
        1,
        state="CREATED",
        search="searchable",
        before_created_at=datetime.fromisoformat(first[0]["created_at"]),
        before_id=uuid.UUID(first[0]["id"]),
    )
    assert [row["id"] for row in second] == [str(older)]
