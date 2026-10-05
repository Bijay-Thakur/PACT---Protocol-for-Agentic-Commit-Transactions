"""Authority / capability engine.

Answers: is this actor authorized to propose and eventually commit this effect
under the current transaction scope? Enforces ``child_authority <= parent_authority``
across the whole delegation chain and tracks cumulative exposure across children.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.config import Settings
from app.core.snapshot import CapNode, EffectNode, TreeSnapshot
from app.domain.capability import AuthorityViolationItem, CapabilitySpec, RootCapabilityGrant

V = AuthorityViolationItem


def pattern_covers(parent: str, child: str) -> bool:
    """Is every resource matched by ``child`` also matched by ``parent``?

    Patterns are exact identifiers or prefixes ending in a single ``*``.
    """
    if parent == "*":
        return True
    if parent.endswith("*"):
        prefix = parent[:-1]
        return child[:-1].startswith(prefix) if child.endswith("*") else child.startswith(prefix)
    return not child.endswith("*") and child == parent


def resource_allowed(resource: str, patterns: tuple[str, ...] | list[str]) -> bool:
    return any(pattern_covers(p, resource) for p in patterns)


def _expired(expires_at: datetime | None, now: datetime) -> bool:
    return expires_at is not None and expires_at <= now


@dataclass
class ExposureResult:
    capability_id: UUID
    transaction_id: UUID
    subject_id: str
    limit: Decimal | None
    exposure: Decimal
    contributions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.limit is None or self.exposure <= self.limit


class AuthorityEngine:
    def __init__(self, settings: Settings):
        self.settings = settings

    # -- issuance ----------------------------------------------------------
    def validate_root_grant(self, grant: RootCapabilityGrant, known_effect_types: set[str], now: datetime) -> list[V]:
        out: list[V] = []
        policy = self.settings.issuer_policies.get(grant.issuer)
        if policy is None:
            return [V(code="UNKNOWN_ISSUER", detail=f"issuer {grant.issuer!r} is not trusted to grant root authority")]
        max_amount = Decimal(str(policy["max_amount"]))
        for label, value in (("amount_limit", grant.amount_limit), ("cumulative_amount_limit", grant.cumulative_amount_limit)):
            if value is None or value > max_amount:
                out.append(V(code="ISSUER_AMOUNT_EXCEEDED",
                             detail=f"{label} must be set and <= issuer maximum {max_amount}",
                             observed={label: str(value) if value is not None else None, "issuer_max": str(max_amount)}))
        if grant.delegation_depth > int(policy.get("max_delegation_depth", 2)):
            out.append(V(code="DELEGATION_DEPTH_EXCEEDED", detail="issuer delegation depth exceeded"))
        unknown = sorted(set(grant.allowed_effect_types) - known_effect_types)
        if unknown:
            out.append(V(code="UNKNOWN_EFFECT_TYPE", detail="capability names unregistered effect types",
                         observed={"unknown": unknown}))
        if _expired(grant.expires_at, now):
            out.append(V(code="CAPABILITY_EXPIRED", detail="grant is already expired"))
        return out

    # -- delegation --------------------------------------------------------
    def validate_delegation(self, parent: CapNode, child: CapabilitySpec, now: datetime) -> list[V]:
        out: list[V] = []
        if _expired(parent.expires_at, now):
            out.append(V(code="PARENT_CAPABILITY_EXPIRED", detail="parent capability has expired"))
        if parent.delegation_depth <= 0:
            out.append(V(code="DELEGATION_DEPTH_EXCEEDED", detail="parent capability may not be delegated further",
                         observed={"parent_depth": parent.delegation_depth}))
        elif child.delegation_depth > parent.delegation_depth - 1:
            out.append(V(code="DELEGATION_DEPTH_EXCEEDED", detail="child delegation depth must be < parent depth",
                         observed={"parent_depth": parent.delegation_depth, "child_depth": child.delegation_depth}))
        extra = sorted(set(child.allowed_effect_types) - set(parent.allowed_effect_types))
        if extra:
            out.append(V(code="EFFECT_TYPE_EXPANSION", detail="child requests effect types the parent lacks",
                         observed={"added": extra}))
        broader = [r for r in child.allowed_resources if not resource_allowed(r, parent.allowed_resources)]
        if broader:
            out.append(V(code="RESOURCE_SCOPE_BROADENED", detail="child resource scope exceeds parent scope",
                         observed={"broadened": broader, "parent_scope": list(parent.allowed_resources)}))
        for label, parent_v, child_v in (
            ("amount_limit", parent.amount_limit, child.amount_limit),
            ("cumulative_amount_limit", parent.cumulative_limit, child.cumulative_amount_limit),
        ):
            if parent_v is not None and (child_v is None or child_v > parent_v):
                out.append(V(code="AMOUNT_ESCALATION", detail=f"child {label} exceeds parent",
                             observed={"parent": str(parent_v), "child": str(child_v) if child_v is not None else "unbounded"}))
        if parent.expires_at is not None and (child.expires_at is None or child.expires_at > parent.expires_at):
            out.append(V(code="EXPIRY_EXTENDED", detail="child capability outlives parent"))
        return out

    def check_chain(self, cap: CapNode, caps: dict[UUID, CapNode], now: datetime) -> list[V]:
        """Re-validate every delegation link up to the root (no escalation anywhere)."""
        out: list[V] = []
        cur = cap
        while cur.parent_capability_id is not None:
            parent = caps.get(cur.parent_capability_id)
            if parent is None:
                out.append(V(code="BROKEN_DELEGATION_CHAIN", detail=f"parent of capability {cur.id} not found"))
                break
            spec = CapabilitySpec(
                allowed_effect_types=list(cur.allowed_effect_types), allowed_resources=list(cur.allowed_resources),
                amount_limit=cur.amount_limit, cumulative_amount_limit=cur.cumulative_limit,
                expires_at=cur.expires_at, delegation_depth=cur.delegation_depth,
            )
            for v in self.validate_delegation(parent, spec, now):
                out.append(V(code="ESCALATION_IN_CHAIN", detail=f"{cur.subject_id}: {v.detail}",
                             observed={"link_code": v.code, **v.observed}))
            cur = parent
        if _expired(cur.expires_at, now) and cur is not cap:
            out.append(V(code="ROOT_CAPABILITY_EXPIRED", detail="root capability has expired"))
        return out

    # -- effects -----------------------------------------------------------
    def check_effect(self, cap: CapNode | None, effect: EffectNode, now: datetime) -> list[V]:
        if cap is None:
            return [V(code="NO_CAPABILITY", detail="transaction has no capability bound")]
        out: list[V] = []
        if _expired(cap.expires_at, now):
            out.append(V(code="CAPABILITY_EXPIRED", detail="capability has expired"))
        if effect.actor_id != cap.subject_id:
            out.append(V(code="ACTOR_NOT_CAPABILITY_SUBJECT", detail="effect actor is not the capability subject",
                         observed={"actor": effect.actor_id, "subject": cap.subject_id}))
        if effect.effect_type not in cap.allowed_effect_types:
            out.append(V(code="EFFECT_TYPE_NOT_AUTHORIZED", detail=f"{effect.effect_type} not in capability",
                         observed={"allowed": list(cap.allowed_effect_types)}))
        denied = [c.resource for c in effect.claims if not resource_allowed(c.resource, cap.allowed_resources)]
        if denied:
            out.append(V(code="RESOURCE_NOT_AUTHORIZED", detail="effect claims resources outside capability scope",
                         observed={"denied": denied, "scope": list(cap.allowed_resources)}))
        if effect.amount is not None and cap.amount_limit is not None and effect.amount > cap.amount_limit:
            out.append(V(code="AMOUNT_EXCEEDS_CAPABILITY", detail="effect amount exceeds capability amount_limit",
                         observed={"amount": str(effect.amount), "amount_limit": str(cap.amount_limit)}))
        return out

    # -- cumulative exposure ----------------------------------------------
    def cumulative_exposure(self, snap: TreeSnapshot) -> list[ExposureResult]:
        """Sum effect exposure (observed / conservative / requested) under every capability's subtree."""
        results: list[ExposureResult] = []
        for tx in sorted(snap.txs.values(), key=lambda t: (t.depth, t.created_at, str(t.id))):
            cap = snap.cap_of(tx.id)
            if cap is None:
                continue
            contributions: dict[UUID, Decimal] = {}
            for e in snap.effects_in(snap.subtree_ids(tx.id)):
                amt = e.exposure()
                if amt is not None:
                    contributions[e.transaction_id] = contributions.get(e.transaction_id, Decimal("0")) + amt
            total = sum(contributions.values(), Decimal("0"))
            results.append(ExposureResult(
                capability_id=cap.id, transaction_id=tx.id, subject_id=cap.subject_id,
                limit=cap.cumulative_limit, exposure=total,
                contributions=[{"transaction_id": str(t), "actor_id": snap.txs[t].actor_id, "amount": str(a)}
                               for t, a in sorted(contributions.items(), key=lambda kv: snap.txs[kv[0]].actor_id)],
            ))
        return results
