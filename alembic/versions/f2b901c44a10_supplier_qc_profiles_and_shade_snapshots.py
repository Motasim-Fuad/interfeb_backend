"""supplier QC profiles and shade snapshots

Revision ID: f2b901c44a10
Revises: d5f84f7c9a21
Create Date: 2026-08-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f2b901c44a10"
down_revision: Union[str, None] = "d5f84f7c9a21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "supplier_qc_profiles",
        sa.Column("supplier_id", sa.UUID(), nullable=False),
        sa.Column("qc_config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("supplier_id", name="uq_supplier_qc_profile_supplier"),
    )
    op.create_index(op.f("ix_supplier_qc_profiles_id"), "supplier_qc_profiles", ["id"], unique=False)
    op.create_index(op.f("ix_supplier_qc_profiles_supplier_id"), "supplier_qc_profiles", ["supplier_id"], unique=False)

    op.add_column("fabric_inspection_roll_defects", sa.Column("defect_type_name_snapshot", sa.String(), nullable=True))
    op.add_column("fabric_inspection_roll_defects", sa.Column("range_label_snapshot", sa.String(), nullable=True))
    op.add_column("fabric_correlation_roll_defects", sa.Column("defect_type_name_snapshot", sa.String(), nullable=True))
    op.add_column("fabric_correlation_roll_defects", sa.Column("range_label_snapshot", sa.String(), nullable=True))

    op.add_column("shade_check_reports", sa.Column("points_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.add_column("shade_check_reports", sa.Column("qc_setup_version_snapshot", sa.Integer(), nullable=False, server_default="1"))

    op.add_column("shade_check_results", sa.Column("point_key_snapshot", sa.String(), nullable=True))
    op.add_column("shade_check_results", sa.Column("point_label_snapshot", sa.String(), nullable=True))
    op.add_column("shade_check_results", sa.Column("point_type_snapshot", sa.String(), nullable=True))
    # Preserve any existing history before master definitions are later edited/deactivated.
    op.execute("""
        UPDATE shade_check_results r
        SET point_key_snapshot = d.check_key,
            point_label_snapshot = d.label,
            point_type_snapshot = CAST(d.check_type AS TEXT)
        FROM shade_check_point_definitions d
        WHERE r.point_definition_id = d.id
    """)


def downgrade() -> None:
    op.drop_column("shade_check_results", "point_type_snapshot")
    op.drop_column("shade_check_results", "point_label_snapshot")
    op.drop_column("shade_check_results", "point_key_snapshot")
    op.drop_column("fabric_correlation_roll_defects", "range_label_snapshot")
    op.drop_column("fabric_correlation_roll_defects", "defect_type_name_snapshot")
    op.drop_column("fabric_inspection_roll_defects", "range_label_snapshot")
    op.drop_column("fabric_inspection_roll_defects", "defect_type_name_snapshot")
    op.drop_column("shade_check_reports", "qc_setup_version_snapshot")
    op.drop_column("shade_check_reports", "points_snapshot")
    op.drop_index(op.f("ix_supplier_qc_profiles_supplier_id"), table_name="supplier_qc_profiles")
    op.drop_index(op.f("ix_supplier_qc_profiles_id"), table_name="supplier_qc_profiles")
    op.drop_table("supplier_qc_profiles")
