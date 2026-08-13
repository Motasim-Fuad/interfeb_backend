"""four point quantity unit snapshot

Revision ID: aa7c3f91b2de
Revises: f2b901c44a10
Create Date: 2026-08-12
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "aa7c3f91b2de"
down_revision: Union[str, None] = "f2b901c44a10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column(
        "fabric_inspection_reports",
        sa.Column("quantity_unit_snapshot", sa.String(), nullable=False, server_default="yds"),
    )

def downgrade() -> None:
    op.drop_column("fabric_inspection_reports", "quantity_unit_snapshot")
