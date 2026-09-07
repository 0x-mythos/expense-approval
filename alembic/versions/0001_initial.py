"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_employee", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_approver", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("approver_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )

    op.create_table(
        "expense_claims",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("submitter_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("approver_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("payment_details", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("decision_comment", sa.Text(), nullable=True),
        sa.Column("decided_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("ai_status", sa.String(20), nullable=True),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("ai_flag", sa.Boolean(), nullable=True),
        sa.Column("ai_flag_reason", sa.Text(), nullable=True),
        sa.Column("ai_generated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_claims_approver_status", "expense_claims", ["approver_id", "status"])
    op.create_index("ix_claims_submitter", "expense_claims", ["submitter_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("claim_id", sa.Integer(), sa.ForeignKey("expense_claims.id"), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_claim", "audit_logs", ["claim_id"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("expense_claims")
    op.drop_table("categories")
    op.drop_table("users")
