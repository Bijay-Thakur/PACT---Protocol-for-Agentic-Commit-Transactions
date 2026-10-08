"""Trace-bound semantic records and non-circular revision binding.

Legacy rows remain explicitly unassessed: nullable source and semantic columns
are not backfilled with invented evidence.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("proposal_traces", sa.Column("source_text", sa.Text(), nullable=True))
    op.add_column("proposal_traces", sa.Column("source_reference", sa.String(500), nullable=True))
    op.add_column("intent_acceptances", sa.Column("accepted_objective", sa.Text(), nullable=True))
    op.add_column("intent_acceptances", sa.Column("intent_semantics", postgresql.JSONB(), nullable=True))
    op.add_column(
        "intent_acceptances",
        sa.Column("resolved_issue_codes", postgresql.JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column("plan_revisions", sa.Column("candidate_digest", sa.String(64), nullable=True))
    op.add_column("plan_revisions", sa.Column("semantic_assessment_hash", sa.String(64), nullable=True))
    op.add_column("plan_revisions", sa.Column("semantic_disposition", sa.String(32), nullable=True))

    op.create_table(
        "semantic_assessments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("root_id", sa.Uuid(), sa.ForeignKey("transactions.id"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("prepare_generation", sa.Integer(), nullable=False),
        sa.Column("candidate_digest", sa.String(64), nullable=False),
        sa.Column("assessment_hash", sa.String(64), nullable=False),
        sa.Column("aggregate", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(256), nullable=True),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("rubric_version", sa.String(32), nullable=False),
        sa.Column("configuration_version", sa.String(64), nullable=False),
        sa.Column("assessment", postgresql.JSONB(), nullable=False),
        sa.Column("usage", postgresql.JSONB(), nullable=False),
        sa.Column("latency_ms", sa.Numeric(12, 1), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "candidate_digest", "provider", "model", "prompt_version",
            "rubric_version", "configuration_version", name="uq_semantic_assessment_cache",
        ),
    )
    op.create_index("ix_semantic_assessments_root_id", "semantic_assessments", ["root_id"])
    op.create_table(
        "semantic_adjudications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("root_id", sa.Uuid(), sa.ForeignKey("transactions.id"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("candidate_digest", sa.String(64), nullable=False),
        sa.Column("assessment_id", sa.Uuid(), sa.ForeignKey("semantic_assessments.id"), nullable=False),
        sa.Column("principal_id", sa.Uuid(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("issue_codes", postgresql.JSONB(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_semantic_adjudications_root_id", "semantic_adjudications", ["root_id"])


def downgrade() -> None:
    op.drop_index("ix_semantic_adjudications_root_id", table_name="semantic_adjudications")
    op.drop_table("semantic_adjudications")
    op.drop_index("ix_semantic_assessments_root_id", table_name="semantic_assessments")
    op.drop_table("semantic_assessments")
    op.drop_column("plan_revisions", "semantic_disposition")
    op.drop_column("plan_revisions", "semantic_assessment_hash")
    op.drop_column("plan_revisions", "candidate_digest")
    op.drop_column("intent_acceptances", "resolved_issue_codes")
    op.drop_column("intent_acceptances", "intent_semantics")
    op.drop_column("intent_acceptances", "accepted_objective")
    op.drop_column("proposal_traces", "source_reference")
    op.drop_column("proposal_traces", "source_text")
