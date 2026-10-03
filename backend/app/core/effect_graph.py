"""Dependency / effect graph manager.

Effects form a DAG keyed by stable ``operation_key``. The graph provides cycle
detection, deterministic topological order (ties broken by operation_key),
readiness (all dependencies VERIFIED - not merely dispatched), execution levels
for display, and reverse order for compensation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from app.core.snapshot import EffectNode
from app.domain.enums import EffectState


@dataclass
class GraphValidation:
    valid: bool
    cycles: list[list[str]] = field(default_factory=list)
    unresolved: dict[str, list[str]] = field(default_factory=dict)
    duplicates: list[str] = field(default_factory=list)


class EffectGraph:
    def __init__(self, effects: list[EffectNode]):
        self.effects = {e.id: e for e in effects}
        self._by_key: dict[str, UUID] = {}
        self.duplicates: list[str] = []
        for e in sorted(effects, key=lambda e: (e.operation_key, str(e.id))):
            if e.operation_key in self._by_key:
                self.duplicates.append(e.operation_key)
            else:
                self._by_key[e.operation_key] = e.id
        self.unresolved: dict[str, list[str]] = {}
        self.deps: dict[UUID, set[UUID]] = {e.id: set() for e in effects}
        for e in effects:
            for key in e.depends_on:
                dep = self._by_key.get(key)
                if dep is None:
                    self.unresolved.setdefault(e.operation_key, []).append(key)
                elif dep != e.id:
                    self.deps[e.id].add(dep)
                else:
                    self.unresolved.setdefault(e.operation_key, []).append(f"{key} (self)")
        self.dependents: dict[UUID, set[UUID]] = {eid: set() for eid in self.effects}
        for eid, ds in self.deps.items():
            for d in ds:
                self.dependents[d].add(eid)

    def _key(self, eid: UUID) -> tuple[str, str]:
        return (self.effects[eid].operation_key, str(eid))

    def edges(self) -> list[tuple[UUID, UUID]]:
        """(prerequisite, dependent) pairs."""
        return sorted(((d, e) for e, ds in self.deps.items() for d in ds), key=lambda p: (self._key(p[1]), self._key(p[0])))

    def find_cycles(self) -> list[list[str]]:
        WHITE, GREY, BLACK = 0, 1, 2
        color = {eid: WHITE for eid in self.effects}
        cycles: list[list[str]] = []
        stack: list[UUID] = []

        def visit(n: UUID) -> None:
            color[n] = GREY
            stack.append(n)
            for d in sorted(self.deps[n], key=self._key):
                if color[d] == GREY:
                    i = stack.index(d)
                    cycles.append([self.effects[x].operation_key for x in stack[i:]] + [self.effects[d].operation_key])
                elif color[d] == WHITE:
                    visit(d)
            stack.pop()
            color[n] = BLACK

        for eid in sorted(self.effects, key=self._key):
            if color[eid] == WHITE:
                visit(eid)
        return cycles

    def validate(self) -> GraphValidation:
        cycles = self.find_cycles()
        return GraphValidation(
            valid=not cycles and not self.unresolved and not self.duplicates,
            cycles=cycles, unresolved=dict(self.unresolved), duplicates=list(self.duplicates),
        )

    def topological_order(self) -> list[EffectNode]:
        """Kahn's algorithm with deterministic tie-breaking. Raises on cycles."""
        indeg = {eid: len(ds) for eid, ds in self.deps.items()}
        ready = sorted((eid for eid, n in indeg.items() if n == 0), key=self._key)
        order: list[UUID] = []
        while ready:
            n = ready.pop(0)
            order.append(n)
            for m in self.dependents[n]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    ready.append(m)
            ready.sort(key=self._key)
        if len(order) != len(self.effects):
            raise ValueError("effect graph contains a cycle")
        return [self.effects[i] for i in order]

    def levels(self) -> dict[UUID, int]:
        level: dict[UUID, int] = {}
        for e in self.topological_order():
            level[e.id] = 1 + max((level[d] for d in self.deps[e.id]), default=-1)
        return level

    def reverse_topological_order(self) -> list[EffectNode]:
        return list(reversed(self.topological_order()))

    def ancestors(self, eid: UUID) -> set[UUID]:
        out, stack = set(), list(self.deps[eid])
        while stack:
            n = stack.pop()
            if n not in out:
                out.add(n)
                stack.extend(self.deps[n])
        return out

    def ordered(self, a: UUID, b: UUID) -> bool:
        """True when a dependency path exists between a and b (in either direction)."""
        return a in self.ancestors(b) or b in self.ancestors(a)

    def unverified_dependencies(self, eid: UUID) -> list[EffectNode]:
        return [self.effects[d] for d in sorted(self.deps[eid], key=self._key)
                if self.effects[d].state != EffectState.VERIFIED]

    def is_ready(self, eid: UUID) -> bool:
        return not self.unverified_dependencies(eid)
