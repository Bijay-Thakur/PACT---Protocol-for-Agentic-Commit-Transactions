"""Deterministic invariant engine.

Each ``expression_type`` maps to a pure evaluator over the tree snapshot. An
invariant attached to a transaction is scoped to that transaction's subtree, so
a root invariant can reference effects proposed by any child agent.

Value references usable in ``limit_refs`` / ``equals_ref``:
- ``capability.amount_limit``            (capability of the effect's own transaction)
- ``root.capability.amount_limit`` / ``root.capability.cumulative_amount_limit``
- ``observation:<effect_type>.<key>``    (evidence captured by that effect's prepare)
- ``metadata.<key>``                     (root transaction metadata)
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from app.core.effect_graph import EffectGraph
from app.core.snapshot import EffectNode, InvariantNode, TreeSnapshot
from app.core.state_machine import EXPOSURE_EFFECT_STATES
from app.domain.enums import EffectState, InvariantPhase
from app.domain.invariant import InvariantEvaluation

Result = tuple[bool, dict[str, Any], str]


def _dec(v: Any) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


class InvariantEngine:
    def __init__(self) -> None:
        self._evaluators: dict[str, Callable[..., Result]] = {
            "effect_amount_lte": self._effect_amount_lte,
            "sum_amount_lte": self._sum_amount_lte,
            "field_matches": self._field_matches,
            "unique_operation_keys": self._unique_operation_keys,
            "depends_on_verified": self._depends_on_verified,
            "implies_verified": self._implies_verified,
            "single_external_match": self._single_external_match,
        }

    def evaluate(
        self, snap: TreeSnapshot, graph: EffectGraph, phase: InvariantPhase,
        now: datetime, scope_tx_id=None,
    ) -> list[InvariantEvaluation]:
        out: list[InvariantEvaluation] = []
        for inv in sorted(snap.invariants, key=lambda i: (i.definition.key, str(i.id))):
            d = inv.definition
            if d.phase != phase:
                continue
            if scope_tx_id is not None and inv.transaction_id != scope_tx_id:
                continue
            effects = snap.effects_in(snap.subtree_ids(inv.transaction_id))
            fn = self._evaluators[d.expression_type]
            try:
                passed, observed, reason = fn(snap, graph, inv, effects, phase)
            except Exception as exc:  # an evaluator bug must fail closed, never open
                passed, observed, reason = False, {"error": repr(exc)}, "evaluator error (fail closed)"
            out.append(InvariantEvaluation(
                invariant_id=inv.id, invariant_key=d.key, name=d.name, phase=phase, passed=passed,
                observed_values=observed, reason=reason, failure_action=d.failure_action, evaluated_at=now,
            ))
        return out

    # -- reference resolution ---------------------------------------------
    def _ref(self, snap: TreeSnapshot, ref: str, effect: EffectNode | None) -> Any:
        if ref == "capability.amount_limit" and effect is not None:
            cap = snap.cap_of(effect.transaction_id)
            return cap.amount_limit if cap else None
        if ref.startswith("root.capability."):
            cap = snap.cap_of(snap.root.id)
            attr = ref.removeprefix("root.capability.")
            return {"amount_limit": cap.amount_limit, "cumulative_amount_limit": cap.cumulative_limit}.get(attr) if cap else None
        if ref.startswith("observation:"):
            # Effect types contain dots ("subscription.cancel"); the key is the last segment.
            etype, _, key = ref.removeprefix("observation:").rpartition(".")
            for e in sorted(snap.effects, key=lambda e: e.operation_key):
                if e.effect_type == etype and key in e.prepare_evidence:
                    return e.prepare_evidence[key]
            return None
        if ref.startswith("metadata."):
            return snap.root.meta.get(ref.removeprefix("metadata."))
        raise ValueError(f"unknown reference {ref!r}")

    # -- evaluators --------------------------------------------------------
    def _effect_amount_lte(self, snap, graph, inv: InvariantNode, effects, phase) -> Result:
        cfg = inv.definition.config
        targets = [e for e in effects if e.effect_type == cfg["effect_type"] and e.state in EXPOSURE_EFFECT_STATES]
        per_effect, failures = [], []
        for e in targets:
            limits: dict[str, str | None] = {}
            values: list[Decimal] = []
            if "limit" in cfg:
                limits["literal"] = str(cfg["limit"])
                values.append(Decimal(str(cfg["limit"])))
            missing = []
            for ref in cfg.get("limit_refs", []):
                v = _dec(self._ref(snap, ref, e))
                limits[ref] = str(v) if v is not None else None
                if v is None:
                    missing.append(ref)
                else:
                    values.append(v)
            authorized = min(values) if values else None
            ok = not missing and authorized is not None and e.amount is not None and e.amount <= authorized
            per_effect.append({"operation_key": e.operation_key, "actor_id": e.actor_id,
                               "amount": str(e.amount), "limits": limits,
                               "authorized": str(authorized) if authorized is not None else None})
            if not ok:
                failures.append(e.operation_key if not missing else f"{e.operation_key} (missing {', '.join(missing)})")
        if failures:
            return False, {"effects": per_effect}, f"amount exceeds authorized amount: {', '.join(failures)}"
        return True, {"effects": per_effect}, f"{len(targets)} effect(s) within authorized amount"

    def _sum_amount_lte(self, snap, graph, inv, effects, phase) -> Result:
        cfg = inv.definition.config
        etype = cfg.get("effect_type")
        live = [e for e in effects if e.amount is not None and e.state in EXPOSURE_EFFECT_STATES
                and (etype is None or e.effect_type == etype)]
        total = sum((e.amount for e in live), Decimal("0"))
        limit = Decimal(str(cfg["limit"])) if "limit" in cfg else _dec(self._ref(snap, cfg["limit_ref"], None))
        observed = {
            "sum": str(total), "limit": str(limit) if limit is not None else None,
            "contributions": [{"actor_id": e.actor_id, "operation_key": e.operation_key, "amount": str(e.amount)}
                              for e in sorted(live, key=lambda e: (e.actor_id, e.operation_key))],
        }
        if limit is None:
            return False, observed, "limit unresolved (fail closed)"
        if total > limit:
            return False, observed, f"combined amount {total} exceeds limit {limit}"
        return True, observed, f"combined amount {total} within limit {limit}"

    def _field_matches(self, snap, graph, inv, effects, phase) -> Result:
        cfg = inv.definition.config
        field, expected = cfg["field"], self._ref(snap, cfg["equals_ref"], None)
        mismatched = sorted(e.operation_key for e in effects if field in e.payload and e.payload[field] != expected)
        observed = {"field": field, "expected": expected, "mismatched": mismatched}
        if expected is None:
            return False, observed, "expected value unresolved (fail closed)"
        if mismatched:
            return False, observed, f"{len(mismatched)} effect(s) target a different {field}"
        return True, observed, f"all effects target {field}={expected}"

    def _unique_operation_keys(self, snap, graph, inv, effects, phase) -> Result:
        counts: dict[str, int] = {}
        for e in effects:
            if e.state in EXPOSURE_EFFECT_STATES:
                counts[e.operation_key] = counts.get(e.operation_key, 0) + 1
        dups = sorted(k for k, n in counts.items() if n > 1)
        if dups:
            return False, {"duplicates": dups}, "duplicate logical operations in transaction"
        return True, {"operation_keys": len(counts)}, "every logical operation is unique"

    def _depends_on_verified(self, snap, graph: EffectGraph, inv, effects, phase) -> Result:
        cfg = inv.definition.config
        dependents = [e for e in effects if e.effect_type == cfg["effect_type"] and e.state != EffectState.ABORTED]
        required = [e for e in effects if e.effect_type in cfg["requires"] and e.state != EffectState.ABORTED]
        rows, problems = [], []
        for d in dependents:
            ancestors = graph.ancestors(d.id) if d.id in graph.deps else set()
            for r in required:
                row = {"dependent": d.operation_key, "requires": r.operation_key,
                       "declared_dependency": r.id in ancestors,
                       "required_verified_at": r.verified_at.isoformat() if r.verified_at else None,
                       "dependent_dispatched_at": d.dispatched_at.isoformat() if d.dispatched_at else None}
                rows.append(row)
                if phase in (InvariantPhase.PREPARE, InvariantPhase.PRE_COMMIT):
                    if r.id not in ancestors:
                        problems.append(f"{d.operation_key} does not depend on {r.operation_key}")
                elif d.dispatched_at is not None and (r.verified_at is None or r.verified_at > d.dispatched_at):
                    problems.append(f"{d.operation_key} dispatched before {r.operation_key} was verified")
        if problems:
            return False, {"pairs": rows}, "; ".join(problems)
        return True, {"pairs": rows}, "dependent effects gated on verified prerequisites"

    def _implies_verified(self, snap, graph, inv, effects, phase) -> Result:
        cfg = inv.definition.config
        ifs = [e for e in effects if e.effect_type == cfg["if_effect_type"]]
        thens = [e for e in effects if e.effect_type == cfg["then_effect_type"]]
        observed = {"if": [{"operation_key": e.operation_key, "state": e.state} for e in ifs],
                    "then": [{"operation_key": e.operation_key, "state": e.state} for e in thens]}
        if phase in (InvariantPhase.PREPARE, InvariantPhase.PRE_COMMIT):
            if ifs and not thens:
                return False, observed, f"{cfg['if_effect_type']} proposed without {cfg['then_effect_type']}"
            return True, observed, "implication is structurally satisfiable"
        if any(e.state == EffectState.VERIFIED for e in ifs) and not any(e.state == EffectState.VERIFIED for e in thens):
            return False, observed, f"{cfg['if_effect_type']} verified but {cfg['then_effect_type']} is not"
        return True, observed, "implication holds"

    def _single_external_match(self, snap, graph, inv, effects, phase) -> Result:
        cfg = inv.definition.config
        rows, bad = [], []
        for e in effects:
            if e.effect_type != cfg["effect_type"] or e.state != EffectState.VERIFIED:
                continue
            count = (e.verification_result or {}).get("evidence", {}).get("match_count")
            rows.append({"operation_key": e.operation_key, "external_match_count": count})
            if count != 1:
                bad.append(e.operation_key)
        if bad:
            return False, {"effects": rows}, f"expected exactly one external record for {', '.join(bad)}"
        return True, {"effects": rows}, "exactly one external record per logical operation"
