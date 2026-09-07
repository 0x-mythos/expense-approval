"""merchant + reimbursable on expense_claims

Revision ID: 0004_merchant_reimbursable
Revises: 0003_reports
Create Date: 2026-09-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_merchant_reimbursable"
down_revision: Union[str, None] = "0003_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "expense_claims",
        sa.Column("merchant", sa.String(255), nullable=True),
    )
    op.add_column(
        "expense_claims",
        sa.Column("reimbursable", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("expense_claims", "reimbursable")
    op.drop_column("expense_claims", "merchant")
