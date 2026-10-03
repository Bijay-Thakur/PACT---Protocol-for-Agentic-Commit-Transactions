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
    def __init__(self, http: httpx.AsyncClient, prefix: str = "/api/v1"):
        self.http = http
        self.prefix = prefix

    async def _req(self, method: str, path: str, json: Any = None, params: dict | None = None) -> dict[str, Any]:
        resp = await self.http.request(method, f"{self.prefix}{path}", json=json, params=params)
        body = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            raise PactApiError(resp.status_code, body)
        return body

    async def create_transaction(self, spec: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/transactions", spec)

    async def create_child(self, parent_id: str, spec: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", f"/transactions/{parent_id}/children", spec)

    async def propose_effect(self, tx_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", f"/transactions/{tx_id}/effects", proposal)

    async def prepare(self, tx_id: str) -> dict[str, Any]:
        return await self._req("POST", f"/transactions/{tx_id}/prepare")

    async def commit(self, tx_id: str, *, step_delay_ms: int = 0, background: bool = False) -> dict[str, Any]:
        return await self._req("POST", f"/transactions/{tx_id}/commit",
                               {"step_delay_ms": step_delay_ms, "background": background})

    async def reconcile(self, tx_id: str) -> dict[str, Any]:
        return await self._req("POST", f"/transactions/{tx_id}/reconcile")

    async def get(self, tx_id: str) -> dict[str, Any]:
        return await self._req("GET", f"/transactions/{tx_id}")

    async def receipt(self, tx_id: str) -> dict[str, Any]:
        return await self._req("GET", f"/transactions/{tx_id}/receipt")

    async def operator_action(self, tx_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", f"/transactions/{tx_id}/operator-actions", body)
