"""multiple receipts per claim: receipts table + backfill from legacy columns

Revision ID: 0007_multi_receipts
Revises: 0006_report_lifecycle
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_multi_receipts"
down_revision = "0006_report_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("claim_id", sa.Integer(), sa.ForeignKey("expense_claims.id"), nullable=False, index=True),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    # Backfill: move each claim's single legacy receipt into the new table.
    op.execute(
        "INSERT INTO receipts (claim_id, filename, path, created_at) "
        "SELECT id, receipt_filename, receipt_path, created_at "
        "FROM expense_claims WHERE receipt_path IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_table("receipts")
