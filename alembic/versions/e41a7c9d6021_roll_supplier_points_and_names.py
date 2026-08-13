"""supplier mill points on planned rolls

Revision ID: e41a7c9d6021
Revises: d013f09a2c77
Create Date: 2026-08-12
"""
from alembic import op
import sqlalchemy as sa

revision = "e41a7c9d6021"
down_revision = "d013f09a2c77"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("plm_rolls", sa.Column("supplier_total_point", sa.Numeric(), nullable=True))


def downgrade():
    op.drop_column("plm_rolls", "supplier_total_point")
