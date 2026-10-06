"""HTTP implementation of :class:`PactClient` (works over a network or in-process ASGI)."""

from __future__ import annotations

from typing import Any

import httpx


class PactApiError(Exception):
    def __init__(self, status: int, body: Any):
        err = (body or {}).get("error", {}) if isinstance(body, dict) else {}
        self.status = status
        self.code = err.get("code", "HTTP_ERROR")
        self.details = err.get("details")
        super().__init__(f"{status} {self.code}: {err.get('message', body)}")


class HttpPactClient:
    def __init__(self, http: httpx.AsyncClient, prefix: str = "/api/v1", *, api_key: str | None = None):
        self.http = http
        self.prefix = prefix
        self.api_key = api_key

    async def _req(self, method: str, path: str, json: Any = None, params: dict | None = None,
                   request_id: str | None = None) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if request_id:
            headers["X-PACT-Request-ID"] = request_id
        resp = await self.http.request(method, f"{self.prefix}{path}", json=json, params=params, headers=headers)
        body = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            raise PactApiError(resp.status_code, body)
        return body

    async def create_transaction(self, spec: dict[str, Any], request_id: str | None = None) -> dict[str, Any]:
        return await self._req("POST", "/transactions", spec, request_id=request_id)

    async def create_child(self, parent_id: str, spec: dict[str, Any],
                           request_id: str | None = None) -> dict[str, Any]:
        return await self._req("POST", f"/transactions/{parent_id}/children", spec, request_id=request_id)

    async def propose_effect(self, tx_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", f"/transactions/{tx_id}/effects", proposal)

    async def prepare(self, tx_id: str) -> dict[str, Any]:
        return await self._req("POST", f"/transactions/{tx_id}/prepare")

    async def commit(self, tx_id: str, revision_digest: str, *, step_delay_ms: int = 0) -> dict[str, Any]:
        """Compatibility alias that still requires the exact reviewed digest."""
        return await self._req("POST", f"/transactions/{tx_id}/commit",
                               {"revision_digest": revision_digest, "step_delay_ms": step_delay_ms})

    async def get(self, tx_id: str) -> dict[str, Any]:
        return await self._req("GET", f"/transactions/{tx_id}")

    async def receipt(self, tx_id: str) -> dict[str, Any]:
        return await self._req("GET", f"/transactions/{tx_id}/receipt")

    async def operator_action(self, tx_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", f"/transactions/{tx_id}/operator-actions", body)

    # Phase 2 convenience methods. Identity comes from the API key, and approval
    # remains an operator-only route rather than an agent convenience method.
    async def list_contracts(self, transaction_id: str | None = None) -> dict[str, Any]:
        return await self._req("GET", "/contracts", params={"transaction_id": transaction_id} if transaction_id else None)

    async def list_workflows(self, transaction_id: str | None = None) -> dict[str, Any]:
        return await self._req("GET", "/workflows", params={"transaction_id": transaction_id} if transaction_id else None)

    async def begin(self, workflow: str, business_request: dict[str, Any],
                    objective: str | None = None, request_id: str | None = None) -> dict[str, Any]:
        return await self.create_transaction({"workflow": workflow,
                                              "business_request": business_request,
                                              "objective": objective}, request_id=request_id)

    async def delegate(self, parent_id: str, recipient: str, objective: str,
                       capability: dict[str, Any], required: bool = True,
                       request_id: str | None = None) -> dict[str, Any]:
        return await self.create_child(parent_id, {"recipient": recipient,
                                                    "objective": objective,
                                                    "capability": capability,
                                                    "required": required}, request_id=request_id)

    async def propose(self, tx_id: str, effect_type: str, slot: str,
                      payload: dict[str, Any], request_id: str,
                      expected_draft_version: int | None = None) -> dict[str, Any]:
        return await self._req("POST", f"/transactions/{tx_id}/effects",
                               {"effect_type": effect_type, "slot": slot,
                                "payload": payload,
                                "expected_draft_version": expected_draft_version},
                               request_id=request_id)

    async def request_commit(self, tx_id: str, revision_digest: str) -> dict[str, Any]:
        return await self._req("POST", f"/transactions/{tx_id}/commit",
                               {"revision_digest": revision_digest})

    async def revise(self, tx_id: str, reason: str) -> dict[str, Any]:
        return await self._req("POST", f"/transactions/{tx_id}/revise", {"reason": reason})

    async def withdraw(self, tx_id: str, effect_id: str, reason: str) -> dict[str, Any]:
        return await self._req("POST", f"/transactions/{tx_id}/effects/{effect_id}/withdraw",
                               {"reason": reason})

    async def status(self, tx_id: str) -> dict[str, Any]:
        return await self.get(tx_id)

    async def abort(self, tx_id: str, reason: str) -> dict[str, Any]:
        return await self._req("POST", f"/transactions/{tx_id}/abort", {"reason": reason})


PactClient = HttpPactClient
