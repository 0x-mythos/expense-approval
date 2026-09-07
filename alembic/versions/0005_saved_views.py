"""saved filter views (Expensify 'Saved')

Revision ID: 0005_saved_views
Revises: 0004_merchant_reimbursable
Create Date: 2026-09-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_saved_views"
down_revision: Union[str, None] = "0004_merchant_reimbursable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "saved_views",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("query", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_saved_views_user", "saved_views", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_saved_views_user", table_name="saved_views")
    op.drop_table("saved_views")
