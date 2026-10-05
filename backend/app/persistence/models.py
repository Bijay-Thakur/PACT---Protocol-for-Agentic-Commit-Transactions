"""SQLAlchemy models - PostgreSQL is the durable source of truth.

Patterns used:
- normalized state tables (transactions, effects, capabilities, ...)
- optimistic concurrency via ``version`` columns (SQLAlchemy version_id_col)
- UNIQUE(operation_key) on logical operations
- append-only ``transaction_events`` and immutable ``receipts`` (DB triggers in
  the initial migration reject UPDATE/DELETE)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class TransactionRow(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo", index=True)
    principal_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    workflow: Mapped[str | None] = mapped_column(String(64), nullable=True)
    workflow_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    business_request_key: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quarantined: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quarantine_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("transactions.id"), nullable=True, index=True
    )
    depth: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    capability_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    policy: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version}


class CapabilityRow(Base):
    __tablename__ = "capabilities"

    id: Mapped[uuid.UUID] = _pk()
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("transactions.id"), nullable=False, index=True
    )
    parent_capability_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("capabilities.id"), nullable=True
    )
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    issuer: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    amount_limit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    cumulative_limit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delegation_depth: Mapped[int] = mapped_column(Integer, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = _ts()


class LogicalOperationRow(Base):
    __tablename__ = "logical_operations"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo", index=True)
    # Trusted business identity (Phase 2). For new operations operation_key == identity key.
    operation_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    effect_type: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    business_request_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    slot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    canonical_resource: Mapped[str | None] = mapped_column(String(512), nullable=True)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_root_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    owner_effect_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    provider_idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    verified_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version}


class EffectRow(Base):
    __tablename__ = "effects"

    id: Mapped[uuid.UUID] = _pk()
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("transactions.id"), nullable=False, index=True
    )
    root_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo", index=True)
    # Bound at compile/freeze (Phase 2); NULL while the effect is only a draft proposal.
    logical_operation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("logical_operations.id"), nullable=True, index=True
    )
    operation_key: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    client_operation_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    slot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contract_type: Mapped[str] = mapped_column(String(128), nullable=False)
    contract_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    contract_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    contract_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Outcome truth (Phase 2): three independent dimensions, never collapsed into one flag.
    application: Mapped[str] = mapped_column(String(32), nullable=False, default="NOT_SENT")
    postcondition: Mapped[str] = mapped_column(String(32), nullable=False, default="UNDETERMINED")
    restoration: Mapped[str] = mapped_column(String(32), nullable=False, default="NOT_REQUIRED")
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    observed_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    max_exposure: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    payload_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    canonical_resource: Mapped[str | None] = mapped_column(String(512), nullable=True)
    revision_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    legacy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    resource_claims: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    depends_on: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    reversibility_class: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    prepare_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    dispatch_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    verification_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    reconciliation_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    compensation_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version}


class EffectDependencyRow(Base):
    """Resolved DAG edge: ``to_effect_id`` depends on ``from_effect_id``."""

    __tablename__ = "effect_dependencies"

    from_effect_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("effects.id"), primary_key=True
    )
    to_effect_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("effects.id"), primary_key=True)


class InvariantRow(Base):
    __tablename__ = "invariants"
    __table_args__ = (UniqueConstraint("transaction_id", "key"),)

    id: Mapped[uuid.UUID] = _pk()
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("transactions.id"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    expression_type: Mapped[str] = mapped_column(String(64), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_action: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = _ts()


class InvariantEvaluationRow(Base):
    __tablename__ = "invariant_evaluations"

    id: Mapped[uuid.UUID] = _pk()
    invariant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("invariants.id"), nullable=False, index=True
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    context: Mapped[str] = mapped_column(String(64), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observed_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _ts()


class OperationAttemptRow(Base):
    __tablename__ = "operation_attempts"
    __table_args__ = (UniqueConstraint("logical_operation_id", "kind", "attempt_no"),)

    id: Mapped[uuid.UUID] = _pk()
    logical_operation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("logical_operations.id"), nullable=False, index=True
    )
    effect_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("effects.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    work_item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    worker_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # False when a fenced-out (stale) worker recorded a late result: evidence, not state.
    authoritative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime] = _ts()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TransactionEventRow(Base):
    """Append-only durable transaction history (not an observability log)."""

    __tablename__ = "transaction_events"
    __table_args__ = (Index("ix_events_root_seq", "root_id", "sequence"),)

    sequence: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4, unique=True, nullable=False)
    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    root_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    effect_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _ts()


class CommitDecisionRow(Base):
    __tablename__ = "commit_decisions"

    id: Mapped[uuid.UUID] = _pk()
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("transactions.id"), nullable=False, index=True
    )
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False)
    checks: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    blocking_reasons: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    evaluated_at: Mapped[datetime] = _ts()


class OperatorActionRow(Base):
    __tablename__ = "operator_actions"

    id: Mapped[uuid.UUID] = _pk()
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("transactions.id"), nullable=False, index=True
    )
    effect_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    operator_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = _ts()


class ReceiptRow(Base):
    __tablename__ = "receipts"

    id: Mapped[uuid.UUID] = _pk()
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("transactions.id"), nullable=False, unique=True
    )
    root_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    receipt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    final_state: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _ts()


# =========================================================================== Phase 2


class TenantRow(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _ts()


class PrincipalRow(Base):
    """A server-side identity. Agents, operators and services authenticate AS a principal."""

    __tablename__ = "principals"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    scopes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    grants: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = _ts()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CredentialRow(Base):
    """Verifier only - plaintext secrets are never stored."""

    __tablename__ = "credentials"

    id: Mapped[uuid.UUID] = _pk()
    principal_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("principals.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # API_KEY | PASSWORD
    lookup: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    verifier: Mapped[str] = mapped_column(String(256), nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = _ts()
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of the cookie value
    principal_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("principals.id"), nullable=False)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _ts()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApiRequestRow(Base):
    """Request-level replay protection (client request ids). Not business identity."""

    __tablename__ = "api_requests"
    __table_args__ = (UniqueConstraint("tenant_id", "principal_id", "request_id"),)

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    principal_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    route: Mapped[str] = mapped_column(String(256), nullable=False)
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _ts()


class PlanRevisionRow(Base):
    __tablename__ = "plan_revisions"
    __table_args__ = (UniqueConstraint("root_id", "revision_no"),)

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    root_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("transactions.id"), nullable=False, index=True)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    workflow: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_version: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    compiled: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    compile_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = _ts()
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApprovalRow(Base):
    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    root_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("transactions.id"), nullable=False, index=True)
    revision_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("plan_revisions.id"), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    approver_principal_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("principals.id"), nullable=False)
    approver_name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _ts()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResourceRow(Base):
    """Materialized canonical resource. Rows are locked (in key order) to serialize claims."""

    __tablename__ = "resources"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = _ts()


class ReservationRow(Base):
    __tablename__ = "reservations"
    __table_args__ = (Index("ix_reservations_active", "tenant_id", "resource_key", "status"),)

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("resources.id"), nullable=False)
    resource_key: Mapped[str] = mapped_column(String(512), nullable=False)
    root_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("transactions.id"), nullable=False, index=True)
    effect_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("effects.id"), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ACTIVE | RELEASED | RETAINED
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = _ts()
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BudgetScopeRow(Base):
    __tablename__ = "budget_scopes"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(512), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    limit_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = _ts()


class BudgetEntryRow(Base):
    """Append-only budget ledger: HOLD / RELEASE_HOLD / CONSUME (amounts always positive)."""

    __tablename__ = "budget_entries"

    id: Mapped[uuid.UUID] = _pk()
    scope_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("budget_scopes.id"), nullable=False, index=True)
    root_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    effect_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = _ts()


class WorkItemRow(Base):
    """Durable job queue. One active item per root (partial unique index in migration 0002)."""

    __tablename__ = "work_items"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    root_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("transactions.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow,
                                                 nullable=False)


class ObservationRow(Base):
    """Normalized external evidence. Final checks and receipts cite these ids."""

    __tablename__ = "observations"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    root_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    effect_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    provenance: Mapped[str] = mapped_column(String(16), nullable=False, default="SIMULATED")
    provider_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    consistency: Mapped[str] = mapped_column(String(16), nullable=False)
    application: Mapped[str] = mapped_column(String(32), nullable=False)
    postcondition: Mapped[str] = mapped_column(String(32), nullable=False)
    restoration: Mapped[str | None] = mapped_column(String(32), nullable=True)
    negative_authoritative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    authoritative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    observed_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    observed_at: Mapped[datetime] = _ts()


class ResidualObligationRow(Base):
    __tablename__ = "residual_obligations"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    root_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    effect_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    bound_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    required_remediation: Mapped[str] = mapped_column(Text, nullable=False)
    blocking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    disposition: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    evidence_observation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    resolution: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _ts()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProposalTraceRow(Base):
    __tablename__ = "proposal_traces"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    principal_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    live: Mapped[bool] = mapped_column(Boolean, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    intent_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    proposal: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    root_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    latency_ms: Mapped[Decimal | None] = mapped_column(Numeric(12, 1), nullable=True)
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _ts()


class ReceiptAmendmentRow(Base):
    """Later evidence linked to an immutable receipt. Never rewrites it."""

    __tablename__ = "receipt_amendments"

    id: Mapped[uuid.UUID] = _pk()
    receipt_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("receipts.id"), nullable=False, index=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _ts()
