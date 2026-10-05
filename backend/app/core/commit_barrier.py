"""Global commit barrier.

Decides - deterministically, over one consistent snapshot - whether a root
transaction may cross from prepared work into real-world commitment. Every
condition is reported as a :class:`BarrierCheck`; any failed check contributes
a machine-readable blocking reason and makes the decision ineligible.

No model output participates. The barrier is the only path to execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.adapters.registry import EffectRegistry
from app.core.authority_engine import AuthorityEngine
from app.core.conflict_manager import detect_conflicts
from app.core.effect_graph import EffectGraph
from app.core.invariant_engine import InvariantEngine
from app.core.snapshot import TreeSnapshot
from app.core.state_machine import UNRESOLVED_EFFECT_STATES
from app.domain.decision import BarrierCheck, CommitDecision
from app.domain.enums import EffectState, InvariantPhase, LogicalOperationStatus, TransactionState
from app.domain.invariant import InvariantEvaluation

# Operations that may be (re)dispatched by a new root. FAILED / COMPENSATED identities are
# closed: re-running the same business action needs an explicit new business epoch.
ALLOWED_OP_STATUSES = {
    LogicalOperationStatus.AVAILABLE,
    LogicalOperationStatus.RETRYABLE,
}


@dataclass
class BarrierResult:
    decision: CommitDecision
    invariant_evaluations: list[InvariantEvaluation]
    executable_effect_ids: list


class CommitBarrier:
    def __init__(self, registry: EffectRegistry, authority: AuthorityEngine, invariants: InvariantEngine):
        self.registry = registry
        self.authority = authority
        self.invariants = invariants

    def evaluate(self, snap: TreeSnapshot, now: datetime, *, binding: bool,
                 extra_checks: list[BarrierCheck] | None = None) -> BarrierResult:
        """``extra_checks`` carry Phase 2 inputs the coordinator evaluates against durable state:
        frozen plan digest, approvals, provider freshness, reservations and budget capacity."""
        checks: list[BarrierCheck] = []

        def check(code: str, passed: bool, detail: str = "", *, subject: str | None = None,
                  observed: dict | None = None, reason: str | None = None) -> None:
            checks.append(BarrierCheck(code=code, passed=passed, subject=subject, detail=detail,
                                       observed=observed or {}, blocking_reason=None if passed else (reason or code)))

        root = snap.root
        check("TRANSACTION_STATE_VALID", root.state == TransactionState.PREPARED,
              f"root state is {root.state}", observed={"state": root.state}, reason="TRANSACTION_NOT_PREPARED")

        # Children: required children must be PREPARED; optional unprepared ones are excluded.
        excluded_tx: set = set()
        for tx in sorted(snap.txs.values(), key=lambda t: (t.depth, t.created_at, str(t.id))):
            if tx.id == root.id:
                continue
            prepared = tx.state == TransactionState.PREPARED
            if not prepared and not tx.required:
                excluded_tx |= snap.subtree_ids(tx.id)
                check("CHILD_PREPARED", True, f"optional child {tx.actor_id} is {tx.state}; excluded",
                      subject=tx.actor_id, observed={"state": tx.state, "required": False})
                continue
            check("CHILD_PREPARED", prepared, f"{tx.actor_id} is {tx.state}", subject=tx.actor_id,
                  observed={"transaction_id": str(tx.id), "state": tx.state, "required": tx.required},
                  reason=f"CHILD_NOT_PREPARED:{tx.actor_id}")

        effects = [e for e in snap.effects if e.transaction_id not in excluded_tx and e.state != EffectState.ABORTED]

        # Required effects and contracts.
        required_types = sorted(root.meta.get("required_effect_types", []))
        present = {e.effect_type for e in effects}
        missing = [t for t in required_types if t not in present]
        check("REQUIRED_EFFECTS_PRESENT", bool(effects) and not missing,
              "no effects proposed" if not effects else (f"missing {missing}" if missing else f"{len(effects)} effects present"),
              observed={"required": required_types, "present": sorted(present)},
              reason="REQUIRED_EFFECTS_MISSING")
        unresolved_contracts = sorted({e.effect_type for e in effects if not self.registry.has(e.effect_type)})
        check("EFFECT_CONTRACTS_RESOLVED", not unresolved_contracts,
              "all effects bound to registered contracts" if not unresolved_contracts else f"unknown: {unresolved_contracts}",
              reason="EFFECT_CONTRACT_UNRESOLVED")

        # Dependency DAG.
        graph = EffectGraph(effects)
        gv = graph.validate()
        check("DEPENDENCY_GRAPH_VALID", not gv.cycles and not gv.unresolved,
              "acyclic, all dependencies resolved" if not gv.cycles and not gv.unresolved else "invalid graph",
              observed={"cycles": gv.cycles, "unresolved": gv.unresolved, "edges": len(graph.edges())},
              reason="DEPENDENCY_CYCLE" if gv.cycles else "DEPENDENCY_UNRESOLVED")

        # Authority: per-effect capability checks and chain escalation checks.
        for e in sorted(effects, key=lambda e: e.operation_key):
            violations = self.authority.check_effect(snap.cap_of(e.transaction_id), e, now)
            check("AUTHORITY_VALID", not violations,
                  "; ".join(v.detail for v in violations) or f"{e.actor_id} authorized for {e.effect_type}",
                  subject=e.operation_key, observed={"violations": [v.model_dump() for v in violations]},
                  reason=f"AUTHORITY_VIOLATION:{violations[0].code}:{e.operation_key}" if violations else None)
        for cap in sorted(snap.caps.values(), key=lambda c: (c.subject_id, str(c.id))):
            if cap.transaction_id in excluded_tx:
                continue
            chain = self.authority.check_chain(cap, snap.caps, now)
            check("NO_AUTHORITY_ESCALATION", not chain,
                  "; ".join(v.detail for v in chain) or f"{cap.subject_id} authority <= parent authority",
                  subject=cap.subject_id, observed={"violations": [v.model_dump() for v in chain]},
                  reason=f"AUTHORITY_ESCALATION:{cap.subject_id}")

        # Cumulative authority across children (shared budget).
        for res in self.authority.cumulative_exposure(snap):
            if res.transaction_id in excluded_tx:
                continue
            is_root = res.transaction_id == root.id
            check("GLOBAL_BUDGET" if is_root else "CUMULATIVE_AUTHORITY", res.passed,
                  f"exposure {res.exposure} vs limit {res.limit}", subject=res.subject_id,
                  observed={"exposure": str(res.exposure), "limit": str(res.limit) if res.limit is not None else None,
                            "contributions": res.contributions},
                  reason="GLOBAL_BUDGET_EXCEEDED" if is_root else f"CUMULATIVE_LIMIT_EXCEEDED:{res.subject_id}")

        # Resource claims.
        conflicts = detect_conflicts(snap, graph, lambda t: self.registry.contract(t) if self.registry.has(t) else None)
        check("RESOURCE_CLAIMS_COMPATIBLE", not conflicts,
              "no incompatible claims" if not conflicts else f"{len(conflicts)} conflict(s)",
              observed={"conflicts": [c.as_dict() for c in conflicts]},
              reason=conflicts[0].code if conflicts else None)
        for c in conflicts[1:]:
            check("RESOURCE_CLAIMS_COMPATIBLE", False, c.detail, observed=c.as_dict(), reason=c.code)

        # Invariants (prepare-phase invariants are re-checked against this snapshot).
        evaluations: list[InvariantEvaluation] = []
        if not gv.cycles:
            for phase in (InvariantPhase.PREPARE, InvariantPhase.PRE_COMMIT):
                evaluations += self.invariants.evaluate(snap, graph, phase, now)
        for ev in evaluations:
            check("INVARIANT", ev.passed, ev.reason, subject=ev.invariant_key,
                  observed={"name": ev.name, "phase": ev.phase, **ev.observed_values},
                  reason=f"INVARIANT_FAILED:{ev.invariant_key}")

        # Idempotency / stable operation identity.
        for e in sorted(effects, key=lambda e: e.operation_key):
            op = snap.logical_ops.get(e.logical_operation_id) if e.logical_operation_id else None
            status = LogicalOperationStatus(op.status) if op else LogicalOperationStatus.AVAILABLE
            owned_here = op is not None and op.owner_root_id == root.id
            ok = status in ALLOWED_OP_STATUSES or (owned_here and status == LogicalOperationStatus.RESERVED)
            reason = None
            if status == LogicalOperationStatus.VERIFIED:
                reason = f"OPERATION_ALREADY_VERIFIED:{e.operation_key}"
            elif status in (LogicalOperationStatus.FAILED, LogicalOperationStatus.COMPENSATED) and not owned_here:
                reason = f"OPERATION_CLOSED_NEW_EPOCH_REQUIRED:{e.operation_key}"
            elif not ok:
                reason = f"OPERATION_IN_FLIGHT_ELSEWHERE:{e.operation_key}"
            check("IDEMPOTENCY", ok, f"logical operation status {status}", subject=e.operation_key,
                  observed={"logical_operation_id": str(e.logical_operation_id), "status": status,
                            "owner_root_id": str(op.owner_root_id) if op and op.owner_root_id else None},
                  reason=reason)

        checks.extend(extra_checks or [])

        # Unresolved ambiguity anywhere in the tree.
        unresolved = sorted(e.operation_key for e in snap.effects if e.state in UNRESOLVED_EFFECT_STATES)
        unknown_ops = sorted(op.operation_key for op in snap.logical_ops.values()
                             if op.status == LogicalOperationStatus.UNKNOWN)
        check("NO_BLOCKING_UNKNOWN", not unresolved and not unknown_ops,
              "no unresolved effects" if not unresolved and not unknown_ops else "unresolved outcomes present",
              observed={"unresolved_effects": unresolved, "unknown_operations": unknown_ops},
              reason="UNRESOLVED_UNKNOWN")

        blocking = []
        for c in checks:
            if not c.passed and c.blocking_reason and c.blocking_reason not in blocking:
                blocking.append(c.blocking_reason)
        decision = CommitDecision(
            transaction_id=root.id, eligible=not blocking, evaluated_at=now, binding=binding,
            snapshot_version=root.version, checks=checks, blocking_reasons=blocking,
        )
        return BarrierResult(decision=decision, invariant_evaluations=evaluations,
                             executable_effect_ids=[e.id for e in effects])
