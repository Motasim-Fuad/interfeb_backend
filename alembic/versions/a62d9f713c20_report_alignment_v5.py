"""align reports with source paper forms

Revision ID: a62d9f713c20
Revises: f58e22c7a941
Create Date: 2026-08-12
"""
from alembic import op
import sqlalchemy as sa

revision = "a62d9f713c20"
down_revision = "f58e22c7a941"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("fabric_relaxation_reports", sa.Column("relaxation_hours_snapshot", sa.Integer(), nullable=True))
    op.add_column("fabric_correlation_rolls", sa.Column("factory_length", sa.Numeric(), nullable=True))
    op.add_column("fabric_correlation_rolls", sa.Column("factory_width", sa.Numeric(), nullable=True))


def downgrade():
    op.drop_column("fabric_correlation_rolls", "factory_width")
    op.drop_column("fabric_correlation_rolls", "factory_length")
    op.drop_column("fabric_relaxation_reports", "relaxation_hours_snapshot")
