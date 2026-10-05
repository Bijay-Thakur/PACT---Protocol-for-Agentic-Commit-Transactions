import asyncio
import uuid
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from app.adapters.base import AdapterContext, HttpAdapter, provider_idempotency_key
from app.domain.enums import DispatchOutcome
from app.domain.receipt import canonical_json, receipt_digest


def test_same_semantic_payload_same_hash():
    uid = uuid.uuid4()
    a = {"transaction_id": uid, "amount": Decimal("143.27"), "nested": {"z": 1, "a": [1, 2]},
         "at": datetime(2026, 10, 2, 12, 0, tzinfo=UTC)}
    # Different key order, different but equal representations of the same values.
    b = {"at": datetime(2026, 10, 2, 14, 0, tzinfo=timezone(timedelta(hours=2))),
         "nested": {"a": [1, 2], "z": 1}, "amount": Decimal("143.270"), "transaction_id": str(uid)}
    assert canonical_json(a) == canonical_json(b)
    assert receipt_digest(a) == receipt_digest(b)


def test_hash_excludes_hash_field_and_changes_with_content():
    p = {"final_state": "COMMITTED_VERIFIED", "amount": "143.27"}
    assert receipt_digest(p) == receipt_digest({**p, "receipt_hash": "anything"})
    assert receipt_digest(p) != receipt_digest({**p, "amount": "143.28"})
    assert len(receipt_digest(p)) == 64


def test_idempotency_key_is_stable_per_logical_operation():
    k = "customer:C1/refund:unused-period:2026-10-02"
    assert provider_idempotency_key(k) == provider_idempotency_key(k)
    assert provider_idempotency_key(k) != provider_idempotency_key(k + "x")


class _Probe(HttpAdapter):
    from app.adapters.billing_mock import CONTRACT as contract


async def _classify(handler, timeout=0.2):
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://x") as http:
        return await _Probe()._send(AdapterContext(http=http, timeout_s=timeout), "POST", "/op")


@pytest.mark.parametrize("status,outcome", [
    (201, DispatchOutcome.ACCEPTED),
    (422, DispatchOutcome.REJECTED_DEFINITIVE),
    (503, DispatchOutcome.RESPONSE_LOST),
    (429, DispatchOutcome.RESPONSE_LOST),
    (500, DispatchOutcome.RESPONSE_LOST),  # ambiguous: may have been applied
])
async def test_dispatch_classification(status, outcome):
    r = await _classify(lambda req: httpx.Response(status, json={"id": "x"}))
    assert r.outcome == outcome


async def test_timeout_is_response_lost_not_failure():
    async def slow(req):
        await asyncio.sleep(1)
        return httpx.Response(200, json={})
    r = await _classify(slow, timeout=0.05)
    assert r.outcome == DispatchOutcome.RESPONSE_LOST


async def test_connect_error_is_not_sent():
    def boom(req):
        raise httpx.ConnectError("refused")
    assert (await _classify(boom)).outcome == DispatchOutcome.NOT_SENT
