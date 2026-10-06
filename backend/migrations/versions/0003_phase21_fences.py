"""Phase 2.1 preparation and authorization generations.

Existing approvals are conservatively invalidated until recorded anew.
Historical receipts and in-flight evidence are untouched.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("prepare_generation", sa.Integer(), nullable=False,
                                             server_default="0"))
    op.add_column("principals", sa.Column("authorization_epoch", sa.Integer(), nullable=False,
                                           server_default="1"))
    op.add_column("approvals", sa.Column("authorization_epoch", sa.Integer(), nullable=False,
                                          server_default="0"))
    op.create_table("intent_acceptances",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_trace_id", sa.Uuid(), sa.ForeignKey("proposal_traces.id"), nullable=False),
        sa.Column("root_id", sa.Uuid(), sa.ForeignKey("transactions.id"), nullable=False, unique=True),
        sa.Column("clarified_request", postgresql.JSONB(), nullable=False),
        sa.Column("clarification_note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade() -> None:
    op.drop_table("intent_acceptances")
    op.drop_column("approvals", "authorization_epoch")
    op.drop_column("principals", "authorization_epoch")
    op.drop_column("transactions", "prepare_generation")
