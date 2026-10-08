"""Transaction manager (Phase 2).

Creates root drafts for authorized workflows, delegates narrower authority to
verified recipient principals, records effect proposals in open drafts, and owns
every state mutation (``transition_tx`` / ``transition_effect`` validate against
the central state machine and append an event in the same database transaction).

Authority comes from the authenticated principal's server-side workflow grant -
never from a request body.
"""

from __future__ import annotations

import json
import hashlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Callable
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import provider_idempotency_key
from app.adapters.registry import EffectRegistry
from app.core.authority_engine import AuthorityEngine
from app.core.state_machine import TERMINAL_TX_STATES, assert_effect_transition, assert_tx_transition
from app.domain.capability import CapabilitySpec
from app.domain.effect import EffectProposal
from app.domain.enums import EffectState, EventType, PlanRevisionStatus, PrincipalKind, TransactionState
from app.domain.errors import AuthorityViolation, NotFound, StateConflict, ValidationFailed
from app.domain.resource_claim import merge_claims
from app.domain.receipt import canonical_json
from app.domain.transaction import BeginRequest, DelegateRequest
from app.persistence.db import Database
from app.persistence.models import (
    CapabilityRow,
    ApiRequestRow,
    EffectRow,
    IntentAcceptanceRow,
    OperatorActionRow,
    PlanRevisionRow,
    ProposalTraceRow,
    TransactionRow,
)
from app.persistence.repositories import append_event, cap_node, get_tx, lock_root
from app.policy.workflows import WorkflowRegistry
from app.security.principals import Forbidden, Principal, PrincipalService, reject_forged
from app.telemetry.tracing import span

Clock = Callable[[], datetime]
SPECIFIABLE = {TransactionState.CREATED, TransactionState.SPECIFYING}


def utcnow() -> datetime:
    return datetime.now(UTC)


class TransactionManager:
    def __init__(self, db: Database, registry: EffectRegistry, authority: AuthorityEngine,
                 workflows: WorkflowRegistry, principals: PrincipalService, clock: Clock = utcnow):
        self.db = db
        self.registry = registry
        self.authority = authority
        self.workflows = workflows
        self.principals = principals
        self.clock = clock

    # ------------------------------------------------------------------ states
    def transition_tx(self, s: AsyncSession, tx: TransactionRow, to: TransactionState, reason: str,
                      *, actor: str = "pact", **payload: Any) -> None:
        frm = tx.state
        assert_tx_transition(frm, to)
        tx.state = str(to)
        if to in TERMINAL_TX_STATES:
            tx.finalized_at = self.clock()
        append_event(s, tx, EventType.TRANSACTION_STATE_CHANGED,
                     {"from": frm, "to": str(to), "reason": reason, **payload}, actor=actor)

    def transition_effect(self, s: AsyncSession, tx: TransactionRow, effect: EffectRow, to: EffectState, reason: str,
                          *, event_type: EventType = EventType.EFFECT_STATE_CHANGED, actor: str = "pact",
                          **payload: Any) -> None:
        frm = effect.state
        assert_effect_transition(frm, to)
        effect.state = str(to)
        append_event(s, tx, event_type,
                     {"from": frm, "to": str(to), "reason": reason, "operation_key": effect.operation_key,
                      "effect_type": effect.contract_type, "application": effect.application,
                      "postcondition": effect.postcondition, "restoration": effect.restoration, **payload},
                     effect_id=effect.id, actor=actor)

    def ensure_specifying(self, s: AsyncSession, tx: TransactionRow, reason: str) -> None:
        if tx.state == TransactionState.CREATED:
            self.transition_tx(s, tx, TransactionState.SPECIFYING, reason)
        elif tx.state != TransactionState.SPECIFYING:
            raise StateConflict(f"transaction is {tx.state}; the draft is closed (revise to reopen)",
                                code="SPECIFICATION_CLOSED", details={"state": tx.state})

    # ----------------------------------------------------------------- begin
    async def _request_replay(self, s: AsyncSession, p: Principal, request_id: str | None,
                              route: str, body: dict[str, Any]) -> ApiRequestRow | None:
        if not request_id:
            return None
        if len(request_id) > 128:
            raise ValidationFailed("request ID too long", code="INVALID_REQUEST_ID")
        fingerprint = hashlib.sha256(canonical_json({"route": route, "body": body}).encode()).hexdigest()
        await s.execute(pg_insert(ApiRequestRow).values(
            tenant_id=p.tenant_id, principal_id=p.id, request_id=request_id,
            route=route, body_hash=fingerprint).on_conflict_do_nothing(
            index_elements=["tenant_id", "principal_id", "request_id"]))
        row = (await s.execute(select(ApiRequestRow).where(
            ApiRequestRow.tenant_id == p.tenant_id, ApiRequestRow.principal_id == p.id,
            ApiRequestRow.request_id == request_id).with_for_update())).scalar_one()
        if row.route != route or row.body_hash != fingerprint:
            raise StateConflict("request ID was already used for different content", code="REQUEST_ID_CONFLICT")
        return row

    async def begin(self, p: Principal, req: BeginRequest, request_id: str | None = None,
                    acceptance: tuple[UUID, str, str, dict[str, Any], list[str]] | None = None) -> UUID:
        p.require("tx:begin")
        reject_forged(p, actor_id=req.actor_id, issuer=req.issuer, tenant_id=req.tenant_id)
        wf = self.workflows.get(req.workflow)
        grant = p.workflow_grant(wf.key)
        if grant is None:
            raise Forbidden(f"principal {p.name!r} has no grant for workflow {wf.key!r}", code="WORKFLOW_NOT_GRANTED")
        params = wf.validate_params(req.business_request)
        if req.new_business_epoch:
            p.require("tx:new_epoch")
            if not req.epoch_reason:
                raise ValidationFailed("a new business epoch needs a reason", code="EPOCH_REASON_REQUIRED")
        now = self.clock()
        cap_spec = wf.root_capability(params, grant, now)
        with span("transaction.create", actor_id=p.name) as sp:
            async with self.db.uow() as s:
                if acceptance is not None:
                    trace = await s.get(ProposalTraceRow, acceptance[0])
                    if trace is None or trace.tenant_id != p.tenant_id or trace.principal_id != p.id \
                            or trace.outcome != "PROPOSED" or not trace.proposal \
                            or trace.proposal.get("requested_workflow") != wf.key:
                        raise Forbidden("proposal is not an accepted request of this principal",
                                        code="PROPOSAL_NOT_OWNED")
                replay_body = req.model_dump(mode="json")
                if acceptance is not None:
                    replay_body = {**replay_body, "acceptance": {
                        "proposal_trace_id": str(acceptance[0]), "clarification_note": acceptance[1],
                        "accepted_objective": acceptance[2], "intent_semantics": acceptance[3],
                        "resolved_issue_codes": acceptance[4]}}
                replay = await self._request_replay(s, p, request_id, "begin", replay_body)
                if replay is not None and replay.response:
                    return UUID(replay.response["transaction_id"])
                tx_id = uuid.uuid4()
                cap = CapabilityRow(
                    id=uuid.uuid4(), transaction_id=tx_id, parent_capability_id=None, subject_id=p.name,
                    issuer=f"grant:{p.tenant_id}/{p.name}/{wf.key}",
                    scope={"allowed_effect_types": cap_spec["allowed_effect_types"],
                           "allowed_resources": cap_spec["allowed_resources"]},
                    amount_limit=Decimal(str(cap_spec["amount_limit"])),
                    cumulative_limit=Decimal(str(cap_spec["cumulative_amount_limit"])),
                    expires_at=cap_spec["expires_at"], delegation_depth=cap_spec["delegation_depth"], meta={})
                meta = {**params, "workflow": wf.key, "labels": req.labels,
                        "epoch_request": {"new": req.new_business_epoch, "reason": req.epoch_reason},
                        "required_effect_types": sorted({sl.effect_type for sl in wf.slots if sl.required})}
                objective = req.objective or f"{wf.title} for {params.get('customer_id', '')}".strip()
                tx = TransactionRow(
                    id=tx_id, tenant_id=p.tenant_id, principal_id=p.id, workflow=wf.key, workflow_version=wf.version,
                    root_id=tx_id, parent_id=None, depth=0, objective=objective, actor_id=p.name, capability_id=cap.id,
                    state=str(TransactionState.CREATED), required=True, meta=meta,
                    policy=req.recovery_policy.model_dump(), current_revision=1,
                )
                s.add(tx)
                await s.flush()
                s.add(cap)
                s.add(PlanRevisionRow(tenant_id=p.tenant_id, root_id=tx_id, revision_no=1,
                                      status=str(PlanRevisionStatus.DRAFT), workflow=wf.key,
                                      workflow_version=wf.version, policy_version=wf.policy_version,
                                      created_by=p.id))
                if acceptance is not None:
                    s.add(IntentAcceptanceRow(tenant_id=p.tenant_id, principal_id=p.id,
                                              proposal_trace_id=acceptance[0], root_id=tx_id,
                                              clarified_request=params, clarification_note=acceptance[1],
                                              accepted_objective=acceptance[2],
                                              intent_semantics=acceptance[3],
                                              resolved_issue_codes=acceptance[4]))
                    trace.root_id = tx_id
                await s.flush()
                append_event(s, tx, EventType.TRANSACTION_CREATED, {
                    "objective": objective, "workflow": wf.key, "workflow_version": wf.version,
                    "policy_version": wf.policy_version, "business_request": params,
                    "principal": {"id": str(p.id), "name": p.name, "kind": str(p.kind)},
                    "recovery_policy": req.recovery_policy.model_dump()}, actor=p.name)
                append_event(s, tx, EventType.CAPABILITY_ISSUED, {
                    "capability_id": str(cap.id), "issuer": cap.issuer, "subject": p.name,
                    "source": "server-side workflow grant", **self._cap_summary(cap)}, actor="pact")
                if replay is not None:
                    replay.status_code = 201
                    replay.response = {"transaction_id": str(tx_id)}
            sp.set_attribute("pact.transaction_id", str(tx_id))
        return tx_id

    # -------------------------------------------------------------- delegate
    async def delegate(self, p: Principal, parent_id: UUID, req: DelegateRequest,
                       request_id: str | None = None) -> UUID:
        p.require("tx:delegate")
        reject_forged(p, actor_id=req.actor_id, tenant_id=req.tenant_id)
        recipient = await self.principals.by_name(p.tenant_id, req.recipient)
        if recipient is None or recipient.kind != PrincipalKind.AGENT:
            raise NotFound(f"recipient {req.recipient!r} is not an active agent principal in this tenant",
                           code="RECIPIENT_NOT_FOUND")
        rejection: AuthorityViolation | None = None
        async with self.db.uow() as s:
            parent = await self._tx_for(s, parent_id, p)
            root = await lock_root(s, parent.root_id)
            await s.refresh(parent)
            replay = await self._request_replay(s, p, request_id, f"delegate:{parent_id}",
                                                req.model_dump(mode="json"))
            if replay is not None and replay.response:
                return UUID(replay.response["transaction_id"])
            if parent.principal_id != p.id:
                raise Forbidden("only the actor of the parent transaction may delegate from it", code="NOT_TRANSACTION_ACTOR")
            grant = p.workflow_grant(root.workflow or "") or {}
            allowed = set(grant.get("may_delegate_to", []))
            if parent.id == root.id and recipient.name not in allowed:
                raise Forbidden(f"{p.name!r} may not delegate to {recipient.name!r} in {root.workflow}",
                                code="DELEGATION_TARGET_NOT_ALLOWED", details={"allowed": sorted(allowed)})
            try:
                child = await self._create_child(s, root, parent, recipient, req)
            except AuthorityViolation as exc:
                rejection = exc  # raised before any write
            else:
                if replay is not None:
                    replay.status_code = 201
                    replay.response = {"transaction_id": str(child.id)}
                return child.id
        async with self.db.uow() as s:
            parent = await get_tx(s, parent_id)
            append_event(s, parent, EventType.CAPABILITY_DELEGATION_REJECTED,
                         {"recipient": req.recipient, "violations": rejection.details}, actor=p.name)
        raise rejection

    async def _create_child(self, s: AsyncSession, root: TransactionRow, parent: TransactionRow,
                            recipient: Principal, req: DelegateRequest) -> TransactionRow:
        if root.state in TERMINAL_TX_STATES:
            raise StateConflict("root transaction is finalized", code="TRANSACTION_FINALIZED")
        parent_cap = await s.get(CapabilityRow, parent.capability_id) if parent.capability_id else None
        if parent_cap is None:
            raise AuthorityViolation("parent has no capability to delegate", code="NO_CAPABILITY")
        spec = req.capability.model_copy(update={"expires_at": req.capability.expires_at or parent_cap.expires_at})
        violations = self.authority.validate_delegation(cap_node(parent_cap), spec, self.clock())
        if violations:
            raise AuthorityViolation(
                f"delegation to {recipient.name} rejected: " + ", ".join(sorted({v.code for v in violations})),
                code="DELEGATION_REJECTED", details=[v.model_dump() for v in violations])
        self.ensure_specifying(s, parent, f"child transaction delegated to {recipient.name}")
        if parent.id != root.id:
            self.ensure_specifying(s, root, "draft open")
        child_id = uuid.uuid4()
        cap = CapabilityRow(
            id=uuid.uuid4(), transaction_id=child_id, parent_capability_id=parent_cap.id, subject_id=recipient.name,
            issuer=f"delegation:{parent.actor_id}",
            scope={"allowed_effect_types": spec.allowed_effect_types, "allowed_resources": spec.allowed_resources},
            amount_limit=spec.amount_limit, cumulative_limit=spec.cumulative_amount_limit,
            expires_at=spec.expires_at or parent_cap.expires_at, delegation_depth=spec.delegation_depth,
            meta=spec.metadata)
        tx = TransactionRow(
            id=child_id, tenant_id=root.tenant_id, principal_id=recipient.id, workflow=root.workflow,
            workflow_version=root.workflow_version, root_id=root.id, parent_id=parent.id, depth=parent.depth + 1,
            objective=req.objective, actor_id=recipient.name, capability_id=cap.id,
            state=str(TransactionState.CREATED), required=req.required, meta={}, policy={},
        )
        s.add(tx)
        await s.flush()
        s.add(cap)
        await s.flush()
        append_event(s, parent, EventType.CHILD_TRANSACTION_CREATED,
                     {"child_id": str(child_id), "actor_id": recipient.name, "principal_id": str(recipient.id),
                      "objective": req.objective, "required": req.required}, actor=parent.actor_id)
        append_event(s, tx, EventType.CAPABILITY_DELEGATED,
                     {"capability_id": str(cap.id), "parent_capability_id": str(parent_cap.id),
                      "delegator": parent.actor_id, "subject": recipient.name, **self._cap_summary(cap)},
                     actor=parent.actor_id)
        return tx

    @staticmethod
    def _cap_summary(cap: CapabilityRow) -> dict[str, Any]:
        return {
            "allowed_effect_types": cap.scope["allowed_effect_types"],
            "allowed_resources": cap.scope["allowed_resources"],
            "amount_limit": str(cap.amount_limit) if cap.amount_limit is not None else None,
            "cumulative_limit": str(cap.cumulative_limit) if cap.cumulative_limit is not None else None,
            "delegation_depth": cap.delegation_depth,
            "expires_at": cap.expires_at.isoformat() if cap.expires_at else None,
        }

    # ---------------------------------------------------------------- propose
    async def propose(self, p: Principal, tx_id: UUID, proposal: EffectProposal,
                      request_id: str | None = None) -> tuple[UUID, int]:
        p.require("tx:propose")
        reject_forged(p, actor_id=proposal.actor_id)
        async with self.db.uow() as s:
            tx = await self._tx_for(s, tx_id, p)
            root = await lock_root(s, tx.root_id)
            await s.refresh(tx)
            replay = None
            if request_id:
                fingerprint = hashlib.sha256(canonical_json({"transaction_id": str(tx_id),
                    "proposal": proposal.model_dump(mode="json")}).encode()).hexdigest()
                await s.execute(pg_insert(ApiRequestRow).values(
                    tenant_id=p.tenant_id, principal_id=p.id, request_id=request_id,
                    route="propose", body_hash=fingerprint).on_conflict_do_nothing(
                    index_elements=["tenant_id", "principal_id", "request_id"]))
                replay = (await s.execute(select(ApiRequestRow).where(
                    ApiRequestRow.tenant_id == p.tenant_id, ApiRequestRow.principal_id == p.id,
                    ApiRequestRow.request_id == request_id).with_for_update())).scalar_one()
                if replay.route != "propose" or replay.body_hash != fingerprint:
                    raise StateConflict("request ID was already used for different content", code="REQUEST_ID_CONFLICT")
                if replay.response:
                    return UUID(replay.response["effect_id"]), int(replay.response["revision"])
            if tx.principal_id != p.id:
                raise Forbidden("only the transaction's own principal may propose into it", code="NOT_TRANSACTION_ACTOR")
            if proposal.expected_draft_version is not None and proposal.expected_draft_version != root.current_revision:
                raise StateConflict(f"draft is at revision {root.current_revision}, not "
                                    f"{proposal.expected_draft_version}", code="DRAFT_VERSION_CONFLICT",
                                    details={"current": root.current_revision})
            if root.state not in SPECIFIABLE:
                raise StateConflict(f"root is {root.state}; the draft is closed", code="SPECIFICATION_CLOSED")
            eff = await self._propose(s, root, tx, proposal)
            if replay is not None:
                replay.status_code = 201
                replay.response = {"effect_id": str(eff.id), "revision": root.current_revision}
            return eff.id, root.current_revision

    async def _propose(self, s: AsyncSession, root: TransactionRow, tx: TransactionRow, p: EffectProposal) -> EffectRow:
        with span("effect.propose", transaction_id=tx.id, root_id=root.id, actor_id=tx.actor_id,
                  effect_type=p.effect_type, operation_key=p.operation_key):
            contract = self.registry.contract(p.effect_type)
            try:
                payload_model = contract.payload_model(**p.payload)
            except ValidationError as exc:
                raise ValidationFailed(f"payload invalid for {p.effect_type}", code="EFFECT_PAYLOAD_INVALID",
                                       details=json.loads(exc.json(include_url=False))) from None
            payload = payload_model.model_dump(mode="json")
            amount = Decimal(str(payload[contract.amount_field])) if contract.amount_field else None
            # Canonical claims are derived by the contract; declared claims are informational only.
            claims = merge_claims(contract.required_claims(payload) if contract.required_claims else [])
            self.ensure_specifying(s, tx, f"effect proposed ({p.effect_type})")
            if tx.id != root.id:
                self.ensure_specifying(s, root, "draft open")
            eff_id = uuid.uuid4()
            draft_key = f"draft:{eff_id}"
            effect = EffectRow(
                id=eff_id, tenant_id=root.tenant_id, transaction_id=tx.id, root_id=root.id, logical_operation_id=None,
                operation_key=draft_key, client_operation_key=p.operation_key, slot=p.slot,
                contract_type=p.effect_type, actor_id=tx.actor_id, state=str(EffectState.PROPOSED), payload=payload,
                amount=amount, currency=payload.get("currency") if contract.amount_field else None,
                resource_claims=[c.model_dump(mode="json") for c in claims], depends_on=sorted(set(p.depends_on)),
                reversibility_class=str(contract.reversibility_class),
                provider_idempotency_key=provider_idempotency_key(draft_key), prepare_evidence={},
                revision_no=root.current_revision,
            )
            s.add(effect)
            await s.flush()
            append_event(s, tx, EventType.EFFECT_PROPOSED, {
                "operation_key": draft_key, "client_operation_key": p.operation_key, "slot": p.slot,
                "effect_type": p.effect_type, "actor_id": tx.actor_id, "payload": payload,
                "canonical_claims": effect.resource_claims, "depends_on": effect.depends_on,
                "declared_claims_ignored": [c.model_dump(mode="json") for c in p.resource_claims],
                "draft_revision": root.current_revision}, effect_id=effect.id, actor=tx.actor_id)
            self.transition_effect(s, tx, effect, EffectState.VALIDATED, "schema valid; bound to a registered contract",
                                   event_type=EventType.EFFECT_VALIDATED, contract=contract.public())
            return effect

    async def withdraw(self, p: Principal, tx_id: UUID, effect_id: UUID, reason: str) -> None:
        async with self.db.uow() as s:
            tx = await self._tx_for(s, tx_id, p)
            root = await lock_root(s, tx.root_id)
            await s.refresh(tx)
            if tx.principal_id != p.id:
                raise Forbidden("only the proposing principal may withdraw", code="NOT_TRANSACTION_ACTOR")
            eff = await s.get(EffectRow, effect_id)
            if eff is None or eff.transaction_id != tx.id:
                raise NotFound("effect not found in this transaction", code="EFFECT_NOT_FOUND")
            if root.state not in SPECIFIABLE or EffectState(eff.state) not in (EffectState.PROPOSED, EffectState.VALIDATED):
                raise StateConflict("only draft effects can be withdrawn", code="SPECIFICATION_CLOSED")
            self.transition_effect(s, tx, eff, EffectState.ABORTED, f"withdrawn by {p.name}: {reason}",
                                   event_type=EventType.EFFECT_WITHDRAWN, actor=p.name)

    # ----------------------------------------------------------------- access
    async def _tx_for(self, s: AsyncSession, tx_id: UUID, p: Principal) -> TransactionRow:
        tx = await s.get(TransactionRow, tx_id)
        if tx is None or tx.tenant_id != p.tenant_id:  # cross-tenant ids are indistinguishable from missing (A35)
            raise NotFound(f"transaction {tx_id} not found", code="TRANSACTION_NOT_FOUND")
        return tx

    async def assert_can_read(self, s: AsyncSession, p: Principal, root_id: UUID) -> None:
        root = await s.get(TransactionRow, root_id)
        if root is None or root.tenant_id != p.tenant_id:
            raise NotFound(f"transaction {root_id} not found", code="TRANSACTION_NOT_FOUND")
        if p.has("tx:read_all"):
            return
        participants = set((await s.execute(select(TransactionRow.principal_id).where(
            TransactionRow.root_id == root_id))).scalars())
        if p.id not in participants:
            raise NotFound(f"transaction {root_id} not found", code="TRANSACTION_NOT_FOUND")

    async def record_operator_action(self, s: AsyncSession, tx: TransactionRow, p: Principal, action: str,
                                     note: str, effect_id: UUID | None = None, payload: dict | None = None) -> None:
        s.add(OperatorActionRow(transaction_id=tx.id, effect_id=effect_id, operator_id=p.name, action=action,
                                note=note, payload={"principal_id": str(p.id), "via": p.via, **(payload or {})}))
        append_event(s, tx, EventType.OPERATOR_ACTION_RECORDED,
                     {"operator_id": p.name, "principal_id": str(p.id), "action": action, "note": note,
                      "effect_id": str(effect_id) if effect_id else None, **(payload or {})},
                     effect_id=effect_id, actor=f"operator:{p.name}")

    async def count_roots(self, s: AsyncSession, tenant_id: str) -> int:
        return (await s.execute(select(func.count()).select_from(TransactionRow).where(
            TransactionRow.tenant_id == tenant_id, TransactionRow.parent_id.is_(None)))).scalar_one()


__all__ = ["TransactionManager", "SPECIFIABLE", "CapabilitySpec"]
