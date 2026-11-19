"""Relaxation: several after-relaxation readings per roll (paper form: one every 6 hours).

Revision ID: d4f9b2c6e811
Revises: c3e8a1d5f720
"""
import sqlalchemy as sa
from alembic import op

revision = "d4f9b2c6e811"
down_revision = "c3e8a1d5f720"
branch_labels = None
depends_on = None


def upgrade():
    # [{"time": "2:00 PM", "length": 50.1, "width": 50.1}, ...]; after_relax_* keep the latest one.
    op.add_column("fabric_relaxation_rolls", sa.Column("after_readings", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("fabric_relaxation_rolls", "after_readings")
