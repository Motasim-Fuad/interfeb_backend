"""unique supplier shade keys

Revision ID: f58e22c7a941
Revises: e41a7c9d6021
"""
from alembic import op
import sqlalchemy as sa

revision = "f58e22c7a941"
down_revision = "e41a7c9d6021"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_shade_point_supplier_key",
        "shade_check_point_definitions",
        ["supplier_id", "check_key"],
    )


def downgrade():
    op.drop_constraint(
        "uq_shade_point_supplier_key",
        "shade_check_point_definitions",
        type_="unique",
    )
