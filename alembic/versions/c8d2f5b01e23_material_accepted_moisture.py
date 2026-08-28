"""add accepted moisture on materials

Revision ID: c8d2f5b01e23
Revises: b7c1e4a90d12
Create Date: 2026-08-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c8d2f5b01e23"
down_revision: Union[str, None] = "b7c1e4a90d12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("materials", sa.Column("accepted_moisture", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("materials", "accepted_moisture")
