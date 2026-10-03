"""Deterministic scripted agents for the demo.

Each agent reasons (trivially, here) and proposes effects independently through
the PACT API. None of them can execute anything: the only way an effect reaches
an external system is the root's commit request passing the global barrier.
A model-backed agent would replace the hard-coded proposal with its own
reasoning while using exactly the same client calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.interfaces import PactClient


@dataclass
class ChildAgent:
    """A worker agent that receives delegated authority and proposes its effects."""

    actor_id: str
    spec: dict[str, Any]

    async def run(self, client: PactClient, root_id: str, *, prepare: bool = True) -> str:
        child = await client.create_child(root_id, {
            "actor_id": self.actor_id, "objective": self.spec["objective"],
            "capability": self.spec["capability"], "required": self.spec.get("required", True),
        })
        child_id = child["transaction"]["id"]
        for proposal in self.spec["effects"]:
            await client.propose_effect(child_id, proposal)
        if prepare:
            # Each child prepares independently; local validity is not global validity.
            await client.prepare(child_id)
        return child_id


@dataclass
class RootAgent:
    """Owns the business objective and the root capability; coordinates children."""

    actor_id: str = "root_agent"

    async def open(self, client: PactClient, spec: dict[str, Any]) -> str:
        created = await client.create_transaction(spec)
        return created["transaction"]["id"]

    async def delegate(self, client: PactClient, root_id: str, children: list[ChildAgent]) -> list[str]:
        return [await child.run(client, root_id) for child in children]

    async def prepare_and_commit(self, client: PactClient, root_id: str, *, step_delay_ms: int = 0,
                                 background: bool = False) -> dict[str, Any]:
        await client.prepare(root_id)
        return await client.commit(root_id, step_delay_ms=step_delay_ms, background=background)


def agents_from_specs(child_specs: list[dict[str, Any]]) -> list[ChildAgent]:
    return [ChildAgent(actor_id=s["actor_id"], spec=s) for s in child_specs]
