"""materials master and PLM material snapshot

Revision ID: b7c1e4a90d12
Revises: a62d9f713c20
Create Date: 2026-08-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b7c1e4a90d12"
down_revision: Union[str, None] = "a62d9f713c20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "materials",
        sa.Column("fabric_name", sa.String(), nullable=False),
        sa.Column("dyeing_method", sa.String(), nullable=True),
        sa.Column("primary_fiber", sa.String(), nullable=True),
        sa.Column("fabric_code", sa.String(), nullable=True),
        sa.Column("fiber_type_1", sa.String(), nullable=True),
        sa.Column("fiber_1_pct", sa.String(), nullable=True),
        sa.Column("fiber_type_2", sa.String(), nullable=True),
        sa.Column("fiber_2_pct", sa.String(), nullable=True),
        sa.Column("fiber_type_3", sa.String(), nullable=True),
        sa.Column("fiber_3_pct", sa.String(), nullable=True),
        sa.Column("weave_knit_type", sa.String(), nullable=True),
        sa.Column("warp_count", sa.String(), nullable=True),
        sa.Column("weft_count", sa.String(), nullable=True),
        sa.Column("epi", sa.String(), nullable=True),
        sa.Column("ppi", sa.String(), nullable=True),
        sa.Column("gsm_expected", sa.String(), nullable=True),
        sa.Column("gsm_tolerance", sa.String(), nullable=True),
        sa.Column("fabric_width_expected", sa.String(), nullable=True),
        sa.Column("width_tolerance", sa.String(), nullable=True),
        sa.Column("fabric_length_per_roll", sa.String(), nullable=True),
        sa.Column("shade_color_name", sa.String(), nullable=True),
        sa.Column("shade_code", sa.String(), nullable=True),
        sa.Column("finish", sa.String(), nullable=True),
        sa.Column("handfeel", sa.String(), nullable=True),
        sa.Column("surface_defect_tolerance", sa.String(), nullable=True),
        sa.Column("pattern_design", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_materials_id"), "materials", ["id"], unique=False)

    op.add_column("plms", sa.Column("material_id", sa.UUID(), nullable=True))
    op.add_column("plms", sa.Column("material_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_foreign_key("fk_plms_material_id", "plms", "materials", ["material_id"], ["id"])
    op.create_index(op.f("ix_plms_material_id"), "plms", ["material_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_plms_material_id"), table_name="plms")
    op.drop_constraint("fk_plms_material_id", "plms", type_="foreignkey")
    op.drop_column("plms", "material_snapshot")
    op.drop_column("plms", "material_id")
    op.drop_index(op.f("ix_materials_id"), table_name="materials")
    op.drop_table("materials")
