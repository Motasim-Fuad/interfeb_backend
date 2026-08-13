"""correlation QC snapshots and units

Revision ID: b83d6e5c1f44
Revises: aa7c3f91b2de
Create Date: 2026-08-12
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "b83d6e5c1f44"
down_revision: Union[str, None] = "aa7c3f91b2de"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column("fabric_correlation_reports", sa.Column("standard_snapshot", sa.Numeric(), nullable=True))
    op.add_column("fabric_correlation_reports", sa.Column("scs_snapshot", sa.Integer(), nullable=True))
    op.add_column("fabric_correlation_reports", sa.Column("measurement_unit_snapshot", sa.String(), nullable=False, server_default="inch"))
    op.add_column("fabric_correlation_reports", sa.Column("quantity_unit_snapshot", sa.String(), nullable=False, server_default="yds"))
    op.add_column("fabric_correlation_reports", sa.Column("qc_setup_version_snapshot", sa.Integer(), nullable=False, server_default="1"))

def downgrade() -> None:
    op.drop_column("fabric_correlation_reports", "qc_setup_version_snapshot")
    op.drop_column("fabric_correlation_reports", "quantity_unit_snapshot")
    op.drop_column("fabric_correlation_reports", "measurement_unit_snapshot")
    op.drop_column("fabric_correlation_reports", "scs_snapshot")
    op.drop_column("fabric_correlation_reports", "standard_snapshot")
