"""Skew & Bowing before wash uses length + measurement variation (AC/BD only after wash).

Revision ID: a1c4e7b9d250
Revises: f4b8c6d2e315
"""
import sqlalchemy as sa
from alembic import op

revision = "a1c4e7b9d250"
down_revision = "f4b8c6d2e315"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("skew_bowing_rolls", sa.Column("length_before", sa.Numeric(), nullable=True))
    op.add_column("skew_bowing_rolls", sa.Column("variation_before", sa.Numeric(), nullable=True))


def downgrade():
    op.drop_column("skew_bowing_rolls", "variation_before")
    op.drop_column("skew_bowing_rolls", "length_before")
