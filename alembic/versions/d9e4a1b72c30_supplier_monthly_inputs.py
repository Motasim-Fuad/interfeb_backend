"""supplier monthly communication inputs

Revision ID: d9e4a1b72c30
Revises: c8d2f5b01e23
Create Date: 2026-08-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "d9e4a1b72c30"
down_revision: Union[str, None] = "c8d2f5b01e23"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "supplier_monthly_inputs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_role", sa.String(), nullable=True),
        sa.Column("created_by_name", sa.String(), nullable=True),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("communication_rating", sa.Numeric(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.UniqueConstraint("supplier_id", "year", "month", name="uq_supplier_month_input"),
    )
    op.create_index("ix_supplier_monthly_inputs_supplier_id", "supplier_monthly_inputs", ["supplier_id"])


def downgrade() -> None:
    op.drop_index("ix_supplier_monthly_inputs_supplier_id", table_name="supplier_monthly_inputs")
    op.drop_table("supplier_monthly_inputs")
