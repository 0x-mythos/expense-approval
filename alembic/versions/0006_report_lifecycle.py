"""report lifecycle: draft/outstanding/paid columns

Revision ID: 0006_report_lifecycle
Revises: 0005_saved_views
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_report_lifecycle"
down_revision = "0005_saved_views"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Batch mode so the FK-bearing column also works on SQLite (copy-and-move),
    # not just PostgreSQL. Adding a column with a ForeignKey via plain ALTER is
    # unsupported by SQLite.
    with op.batch_alter_table("reports") as batch_op:
        batch_op.add_column(sa.Column("submitted_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("paid_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("paid_by_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_reports_paid_by_id_users", "users", ["paid_by_id"], ["id"])
    op.execute("UPDATE reports SET status='outstanding' WHERE status='pending'")


def downgrade() -> None:
    op.execute("UPDATE reports SET status='pending' WHERE status IN ('outstanding','draft','paid')")
    with op.batch_alter_table("reports") as batch_op:
        batch_op.drop_constraint("fk_reports_paid_by_id_users", type_="foreignkey")
        batch_op.drop_column("paid_by_id")
        batch_op.drop_column("paid_at")
        batch_op.drop_column("submitted_at")
