"""receipt attachment + AI category suggestion

Revision ID: 0002_receipts_ai
Revises: 0001_initial
Create Date: 2026-09-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_receipts_ai"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("expense_claims", sa.Column("receipt_filename", sa.String(255), nullable=True))
    op.add_column("expense_claims", sa.Column("receipt_path", sa.String(255), nullable=True))
    op.add_column("expense_claims", sa.Column("ai_suggested_category", sa.String(100), nullable=True))
    op.add_column("expense_claims", sa.Column("ai_category_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("expense_claims", "ai_category_reason")
    op.drop_column("expense_claims", "ai_suggested_category")
    op.drop_column("expense_claims", "receipt_path")
    op.drop_column("expense_claims", "receipt_filename")
