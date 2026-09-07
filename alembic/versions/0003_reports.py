"""reports (grouping of claims)

Revision ID: 0003_reports
Revises: 0002_receipts_ai
Create Date: 2026-09-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_reports"
down_revision: Union[str, None] = "0002_receipts_ai"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("submitter_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("approver_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("decision_comment", sa.Text(), nullable=True),
        sa.Column("decided_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_reports_approver_status", "reports", ["approver_id", "status"])
    op.create_index("ix_reports_submitter", "reports", ["submitter_id"])
    # Batch mode so adding this FK column also works on SQLite (copy-and-move),
    # not only PostgreSQL.
    with op.batch_alter_table("expense_claims") as batch_op:
        batch_op.add_column(sa.Column("report_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_expense_claims_report_id_reports", "reports", ["report_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("expense_claims") as batch_op:
        batch_op.drop_constraint("fk_expense_claims_report_id_reports", type_="foreignkey")
        batch_op.drop_column("report_id")
    op.drop_table("reports")
