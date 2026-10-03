"""Resource claim and conflict manager.

Compatibility rules (within one root transaction):
- READ/READ: always compatible.
- WRITE/WRITE and READ/WRITE: compatible only if the two effects are ordered by
  the dependency DAG (a happens-before b); concurrent writes conflict.
- EXCLUSIVE: incompatible with any other claim on the same resource, ordered or not.
- Contradictory effect types (declared by contracts) sharing a resource conflict.
- The same operation_key proposed twice in one tree is a duplicate operation.

Across roots: claims on the same resource held by another active transaction
conflict when either claim is EXCLUSIVE or both are WRITE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Callable

from app.core.effect_graph import EffectGraph
from app.core.snapshot import EffectNode, TreeSnapshot
from app.core.state_machine import EXPOSURE_EFFECT_STATES
from app.domain.effect import EffectContract
from app.domain.enums import ClaimMode


@dataclass
class Conflict:
    code: str
    detail: str
    resources: list[str] = field(default_factory=list)
    operation_keys: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"code": self.code, "detail": self.detail, "resources": self.resources,
                "operation_keys": self.operation_keys}


def detect_conflicts(
    snap: TreeSnapshot, graph: EffectGraph, contract_of: Callable[[str], EffectContract | None],
) -> list[Conflict]:
    live = [e for e in snap.effects if e.state in EXPOSURE_EFFECT_STATES]
    out: list[Conflict] = []

    for key, effects in sorted(snap.effect_by_key().items()):
        if len([e for e in effects if e.state in EXPOSURE_EFFECT_STATES]) > 1:
            out.append(Conflict("DUPLICATE_OPERATION_KEY", f"operation {key} proposed {len(effects)} times",
                                operation_keys=[key]))

    def claims(e: EffectNode) -> dict[str, ClaimMode]:
        return {c.resource: c.mode for c in e.claims}

    for a, b in combinations(sorted(live, key=lambda e: (e.operation_key, str(e.id))), 2):
        if a.operation_key == b.operation_key:
            continue
        ca, cb = claims(a), claims(b)
        shared = sorted(set(ca) & set(cb))
        if not shared:
            continue
        keys = [a.operation_key, b.operation_key]
        contract_a, contract_b = contract_of(a.effect_type), contract_of(b.effect_type)
        if (contract_a and b.effect_type in contract_a.contradicts) or (contract_b and a.effect_type in contract_b.contradicts):
            out.append(Conflict("CONTRADICTORY_EFFECTS",
                                f"{a.effect_type} and {b.effect_type} contradict on {', '.join(shared)}",
                                resources=shared, operation_keys=keys))
            continue
        ordered = graph.ordered(a.id, b.id)
        for r in shared:
            ma, mb = ca[r], cb[r]
            if ClaimMode.EXCLUSIVE in (ma, mb):
                out.append(Conflict("RESOURCE_EXCLUSIVE_CONFLICT", f"exclusive claim on {r} is shared",
                                    resources=[r], operation_keys=keys))
            elif ma == ClaimMode.READ and mb == ClaimMode.READ:
                continue
            elif not ordered:
                out.append(Conflict("RESOURCE_WRITE_CONFLICT",
                                    f"unordered concurrent writes to {r}; declare a dependency to order them",
                                    resources=[r], operation_keys=keys))

    for ext in snap.external_claims:
        for e in live:
            for c in e.claims:
                if c.resource != ext.claim.resource:
                    continue
                modes = {c.mode, ext.claim.mode}
                if ClaimMode.EXCLUSIVE in modes or modes == {ClaimMode.WRITE}:
                    out.append(Conflict("CROSS_TRANSACTION_RESOURCE_CONFLICT",
                                        f"{c.resource} is claimed ({ext.claim.mode}) by active transaction {ext.root_id}",
                                        resources=[c.resource], operation_keys=[e.operation_key, ext.operation_key]))
    return out
