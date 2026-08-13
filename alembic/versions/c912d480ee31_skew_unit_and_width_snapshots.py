"""skew unit and width snapshots

Revision ID: c912d480ee31
Revises: b83d6e5c1f44
Create Date: 2026-08-12
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "c912d480ee31"
down_revision: Union[str, None] = "b83d6e5c1f44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column("skew_bowing_reports", sa.Column("fabric_width_label_snapshot", sa.String(), nullable=True))
    op.add_column("skew_bowing_reports", sa.Column("measurement_unit_snapshot", sa.String(), nullable=False, server_default="inch"))
    op.add_column("skew_bowing_reports", sa.Column("quantity_unit_snapshot", sa.String(), nullable=False, server_default="yds"))
    op.add_column("skew_bowing_reports", sa.Column("qc_setup_version_snapshot", sa.Integer(), nullable=False, server_default="1"))

def downgrade() -> None:
    op.drop_column("skew_bowing_reports", "qc_setup_version_snapshot")
    op.drop_column("skew_bowing_reports", "quantity_unit_snapshot")
    op.drop_column("skew_bowing_reports", "measurement_unit_snapshot")
    op.drop_column("skew_bowing_reports", "fabric_width_label_snapshot")
