"""Independent SDK process proves initialize, discovery and authenticated calls."""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import uuid
from pathlib import Path
from uuid import UUID

import uvicorn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.domain.enums import PrincipalKind
from app.domain.transaction import ApprovalRequest
from app.main import create_app
from app.persistence.models import TransactionRow
from app.worker import Worker

from .conftest import make_settings, requires_db

pytestmark = requires_db


async def test_stdio_mcp_calls_authenticated_rest_from_separate_process(rt):
    tenant = f"test-{uuid.uuid4().hex[:6]}"
    p = await rt.principals.upsert_principal(
        tenant, "mcp_agent", PrincipalKind.AGENT, ["tx:begin", "tx:propose", "tx:prepare", "tx:commit"],
        {"workflows": {"customer_remediation": {"remediation_budget": "100.00"}}})
    key = await rt.principals.issue_api_key(p.id)
    other = await rt.principals.upsert_principal(
        tenant, "other_mcp_agent", PrincipalKind.AGENT, ["tx:begin"],
        {"workflows": {"customer_remediation": {"remediation_budget": "10.00"}}})
    other_key = await rt.principals.issue_api_key(other.id)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = create_app(make_settings(embedded_worker=False, auto_recover_on_startup=False), runtime=rt)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    running = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.02)
        assert server.started
        params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server"],
            cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
            env={**os.environ, "PACT_MCP_API_KEY": key, "PACT_API_URL": f"http://127.0.0.1:{port}"})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                assert {"pact_list_contracts", "pact_begin", "pact_propose", "pact_request_commit",
                        "pact_status", "pact_receipt", "pact_abort"} <= names
                contracts = await session.call_tool("pact_list_contracts")
                assert contracts.structured_content["ok"] is True
                assert {c["effect_type"] for c in contracts.structured_content["result"]["contracts"]} == {
                    "billing.refund"}
                created = await session.call_tool("pact_begin", {"workflow": "customer_remediation",
                    "business_request": {"customer_id": "C-MCP", "incident_id": "INC-MCP"}})
                assert created.structured_content["ok"] is True, created
                rid = created.structured_content["result"]["transaction_id"]
                status = await session.call_tool("pact_status", {"transaction_id": rid})
                assert status.structured_content["result"]["transaction"]["state"] == "CREATED"
                repeated = await session.call_tool("pact_status", {"transaction_id": rid})
                assert repeated.structured_content["result"]["transaction"]["id"] == rid
                malformed = await session.call_tool("pact_begin", {"workflow": "customer_remediation"})
                assert malformed.is_error
        node_dir = Path(__file__).resolve().parents[2] / "examples" / "mcp-node"
        if (node_dir / "node_modules").exists():
            proc = await asyncio.create_subprocess_exec(
                "node", "client.mjs", cwd=str(node_dir),
                env={**os.environ, "PACT_PYTHON": sys.executable,
                     "PACT_MCP_API_KEY": key, "PACT_API_URL": f"http://127.0.0.1:{port}"},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
            assert proc.returncode == 0, stderr.decode()
            assert b"pact_list_contracts" in stdout
        params.env["PACT_MCP_API_KEY"] = other_key
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                forbidden = await session.call_tool("pact_status", {"transaction_id": rid})
                assert forbidden.structured_content["ok"] is False
                assert forbidden.structured_content["http_status"] == 404
    finally:
        server.should_exit = True
        await running


async def test_mcp_disconnect_after_queue_and_replay_does_not_repeat_provider_call(rt):
    tenant = f"test-{uuid.uuid4().hex[:8]}"
    cid = f"C-MCP-{uuid.uuid4().hex[:6].upper()}"
    incident = f"INC-{uuid.uuid4().hex[:6].upper()}"
    agent = await rt.principals.upsert_principal(tenant, "mcp_refund", PrincipalKind.AGENT,
        ["tx:begin", "tx:propose", "tx:prepare", "tx:commit"],
        {"workflows": {"customer_remediation": {"remediation_budget": "100.00"}}})
    approver = await rt.principals.upsert_principal(tenant, "finance", PrincipalKind.OPERATOR,
        ["op:approve"], {"roles": ["finance_approver"]})
    key = await rt.principals.issue_api_key(agent.id)
    (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
    charge = (await rt.http.get(f"/sim/state/{cid}")).json()["billing"]["account"]["charges"][0]["id"]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = create_app(make_settings(embedded_worker=False, auto_recover_on_startup=False), runtime=rt)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    running = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.02)
        assert server.started
        params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server"],
            cwd=str(Path(__file__).resolve().parents[1]),
            env={**os.environ, "PACT_MCP_API_KEY": key, "PACT_API_URL": f"http://127.0.0.1:{port}"})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                begun = await session.call_tool("pact_begin", {"workflow": "customer_remediation",
                    "business_request": {"customer_id": cid, "incident_id": incident}})
                assert begun.structured_content["ok"] is True, begun
                root = begun.structured_content["result"]["transaction_id"]
                proposed = await session.call_tool("pact_propose", {"transaction_id": root,
                    "effect_type": "billing.refund", "slot": "refund_line",
                    "payload": {"customer_id": cid, "charge_id": charge, "amount": "50.00"},
                    "request_id": "refund-proposal-1"})
                assert proposed.structured_content["ok"] is True, proposed
                frozen = await session.call_tool("pact_prepare", {"transaction_id": root})
                assert frozen.structured_content["ok"] is True, frozen
                digest = frozen.structured_content["result"]["digest"]
                await rt.coordinator.approve(approver, UUID(root), ApprovalRequest(
                    revision_digest=digest, reason="Reviewed MCP refund"))
                queued = await session.call_tool("pact_request_commit", {
                    "transaction_id": root, "revision_digest": digest})
                assert queued.structured_content["result"]["status"] == "QUEUED", queued
        # The transport is gone; durable work and business identity remain.
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                status = await session.call_tool("pact_status", {"transaction_id": root})
                assert status.structured_content["result"]["transaction"]["state"] in {
                    "COMMITTING", "VERIFYING", "COMMITTED_VERIFIED"}
                replay = await session.call_tool("pact_request_commit", {
                    "transaction_id": root, "revision_digest": digest})
                assert replay.structured_content["ok"] is True, replay
                assert replay.structured_content["result"]["status"] in {"QUEUED", "ALREADY_TERMINAL"}
                assert replay.structured_content["result"]["replayed"] is True
        worker = Worker(rt)
        for _ in range(30):
            await worker.run_once(UUID(root))
            async with rt.db.read() as session:
                state = (await session.get(TransactionRow, UUID(root))).state
            if state == "COMMITTED_VERIFIED":
                break
        assert state == "COMMITTED_VERIFIED"
        calls = (await rt.http.get("/sim/calls", params={"customer_id": cid,
            "operation": "create_refund"})).json()
        assert len(calls) == 1
    finally:
        server.should_exit = True
        await running
