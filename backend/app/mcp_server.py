"""Official SDK stdio MCP facade over the authenticated PACT REST API.

Each server process receives one agent's API key through its private environment.
The key never appears in a tool schema, model argument or response. No provider
calls or transaction logic live here.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server import MCPServer


def create_server(base_url: str, api_key: str) -> MCPServer:
    if not api_key:
        raise RuntimeError("PACT_MCP_API_KEY must be a private agent credential")
    server = MCPServer("pact", version="2.0.0")

    async def call(method: str, path: str, body: dict[str, Any] | None = None,
                   request_id: str | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {api_key}"}
        if request_id:
            headers["X-PACT-Request-ID"] = request_id
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
                response = await client.request(method, path, json=body, headers=headers)
            data = response.json()
            if response.is_error:
                err = data.get("error", {})
                return {"ok": False, "category": "PACT_REJECTED", "http_status": response.status_code,
                        "code": err.get("code", "HTTP_ERROR"), "message": err.get("message", "request rejected"),
                        "details": err.get("details")}
            return {"ok": True, "result": data}
        except (httpx.HTTPError, ValueError) as exc:
            return {"ok": False, "category": "TRANSPORT", "code": "PACT_UNAVAILABLE",
                    "message": type(exc).__name__}

    @server.tool()
    async def pact_list_contracts() -> dict[str, Any]:
        """List contracts allowed by this agent's server-side workflow grants."""
        return await call("GET", "/api/v1/contracts")

    @server.tool()
    async def pact_begin(workflow: str, business_request: dict[str, Any],
                         objective: str | None = None) -> dict[str, Any]:
        """Create a draft; credentials determine identity and workflow authority."""
        return await call("POST", "/api/v1/transactions",
                          {"workflow": workflow, "business_request": business_request, "objective": objective})

    @server.tool()
    async def pact_delegate(parent_id: str, recipient: str, objective: str,
                            capability: dict[str, Any], required: bool = True) -> dict[str, Any]:
        """Delegate only a subset of the verified parent's capability."""
        return await call("POST", f"/api/v1/transactions/{parent_id}/children",
                          {"recipient": recipient, "objective": objective,
                           "capability": capability, "required": required})

    @server.tool()
    async def pact_propose(transaction_id: str, effect_type: str, slot: str,
                           payload: dict[str, Any], request_id: str,
                           expected_draft_version: int | None = None) -> dict[str, Any]:
        """Append a typed draft effect. The result never means provider application."""
        return await call("POST", f"/api/v1/transactions/{transaction_id}/effects",
                          {"effect_type": effect_type, "slot": slot, "payload": payload,
                           "expected_draft_version": expected_draft_version}, request_id)

    @server.tool()
    async def pact_prepare(transaction_id: str) -> dict[str, Any]:
        """Resolve facts, compile mandatory policy and freeze a reviewable revision."""
        return await call("POST", f"/api/v1/transactions/{transaction_id}/prepare", {})

    @server.tool()
    async def pact_request_commit(transaction_id: str, revision_digest: str) -> dict[str, Any]:
        """Request the binding barrier. QUEUED never means applied or verified."""
        return await call("POST", f"/api/v1/transactions/{transaction_id}/commit",
                          {"revision_digest": revision_digest})

    @server.tool()
    async def pact_status(transaction_id: str) -> dict[str, Any]:
        """Read the durable transaction and effect state within this principal's scope."""
        return await call("GET", f"/api/v1/transactions/{transaction_id}")

    @server.tool()
    async def pact_receipt(transaction_id: str) -> dict[str, Any]:
        """Read a final receipt, or a typed not-final response."""
        return await call("GET", f"/api/v1/transactions/{transaction_id}/receipt")

    @server.tool()
    async def pact_abort(transaction_id: str, reason: str) -> dict[str, Any]:
        """Request a safe abort or post-dispatch recovery."""
        return await call("POST", f"/api/v1/transactions/{transaction_id}/abort", {"reason": reason})

    return server


def main() -> None:
    server = create_server(os.environ.get("PACT_API_URL", "http://127.0.0.1:8000"),
                           os.environ.get("PACT_MCP_API_KEY", ""))
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
