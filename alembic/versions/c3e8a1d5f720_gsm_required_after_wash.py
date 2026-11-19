"""GSM: separate Required GSM for after wash (the mill's finished GSM can differ from greige).

Revision ID: c3e8a1d5f720
Revises: b7d2e4f1a903
"""
import sqlalchemy as sa
from alembic import op

revision = "c3e8a1d5f720"
down_revision = "b7d2e4f1a903"
branch_labels = None
depends_on = None


def upgrade():
    # standard_gsm stays the before-wash value; empty after-wash value = same as before wash.
    op.add_column("gsm_check_reports", sa.Column("standard_gsm_after", sa.Numeric(), nullable=True))


def downgrade():
    op.drop_column("gsm_check_reports", "standard_gsm_after")
