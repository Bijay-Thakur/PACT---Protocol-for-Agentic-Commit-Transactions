"""Agent-side interfaces.

Agents are untrusted relative to PACT commit policy. They hold a
:class:`PactClient` - which can only *propose*, *prepare* and request a commit -
and never receive adapters, database sessions, or provider credentials. Any
framework (LangGraph, CrewAI, AutoGen, OpenAI Agents SDK, MCP tools, custom
Python) integrates by implementing an agent against this client.
"""

from __future__ import annotations

from typing import Any, Protocol


class PactClient(Protocol):
    async def create_transaction(self, spec: dict[str, Any]) -> dict[str, Any]: ...
    async def create_child(self, parent_id: str, spec: dict[str, Any]) -> dict[str, Any]: ...
    async def propose_effect(self, tx_id: str, proposal: dict[str, Any]) -> dict[str, Any]: ...
    async def prepare(self, tx_id: str) -> dict[str, Any]: ...
    async def commit(self, tx_id: str, *, step_delay_ms: int = 0, background: bool = False) -> dict[str, Any]: ...
    async def reconcile(self, tx_id: str) -> dict[str, Any]: ...
    async def get(self, tx_id: str) -> dict[str, Any]: ...


class Agent(Protocol):
    actor_id: str
