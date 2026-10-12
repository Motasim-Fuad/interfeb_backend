"""v8: client_updated_at on every module roll so offline replays keep the newest copy

Revision ID: c3a7d5e1f842
Revises: b4f1c8e2d930
Create Date: 2026-10-12
"""
from alembic import op
import sqlalchemy as sa

revision = "c3a7d5e1f842"
down_revision = "b4f1c8e2d930"
branch_labels = None
depends_on = None

_TABLES = [
    "gsm_check_rolls",
    "moisture_check_rolls",
    "skew_bowing_rolls",
    "fabric_relaxation_rolls",
    "shade_check_rolls",
    "shrinkage_rolls",
    "color_continuity_rolls",
]


def upgrade():
    for table in _TABLES:
        op.add_column(table, sa.Column("client_updated_at", sa.DateTime(), nullable=True))


def downgrade():
    for table in reversed(_TABLES):
        op.drop_column(table, "client_updated_at")
