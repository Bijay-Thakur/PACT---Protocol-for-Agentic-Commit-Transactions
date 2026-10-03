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
    operation_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    effect_type: Mapped[str] = mapped_column(String(128), nullable=False)
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
    logical_operation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("logical_operations.id"), nullable=False, index=True
    )
    operation_key: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    contract_type: Mapped[str] = mapped_column(String(128), nullable=False)
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
