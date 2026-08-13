"""consignment qc setup and roll integrity

Revision ID: d5f84f7c9a21
Revises: c01a8077013f
Create Date: 2026-08-11
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "d5f84f7c9a21"
down_revision: Union[str, None] = "c01a8077013f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _dedupe_simple(table: str, parent_col: str, inspection_fk: str | None = None, child_table: str | None = None, child_fk: str | None = None):
    """Keep newest row per (parent, roll_no), safely re-point master report refs and remove child rows."""
    mapping = f"""
        WITH ranked AS (
            SELECT id, FIRST_VALUE(id) OVER (
                PARTITION BY {parent_col}, roll_no ORDER BY updated_at DESC, created_at DESC, id DESC
            ) AS keep_id,
            ROW_NUMBER() OVER (
                PARTITION BY {parent_col}, roll_no ORDER BY updated_at DESC, created_at DESC, id DESC
            ) AS rn
            FROM {table}
        )
        SELECT id, keep_id FROM ranked WHERE rn > 1
    """
    if inspection_fk:
        op.execute(f"""
            WITH dup AS ({mapping})
            UPDATE inspection_reports ir SET {inspection_fk}=dup.keep_id
            FROM dup WHERE ir.{inspection_fk}=dup.id
        """)
    if child_table and child_fk:
        op.execute(f"""
            WITH dup AS ({mapping})
            DELETE FROM {child_table} c USING dup WHERE c.{child_fk}=dup.id
        """)
    op.execute(f"""
        WITH dup AS ({mapping})
        DELETE FROM {table} t USING dup WHERE t.id=dup.id
    """)


def upgrade() -> None:
    # Consignment becomes the final QC planning context.
    op.add_column("fabric_receives", sa.Column("received_quantity", sa.Numeric(), nullable=True))
    op.add_column("fabric_receives", sa.Column("quantity_unit", sa.String(), nullable=False, server_default="yds"))
    op.add_column("fabric_receives", sa.Column("fabric_type", sa.String(), nullable=True))
    op.add_column("fabric_receives", sa.Column("fabric_composition", sa.String(), nullable=True))
    op.add_column("fabric_receives", sa.Column("measurement_unit", sa.Enum("cm", "inch", name="measurement_unit_enum", create_type=False), nullable=False, server_default="inch"))
    op.add_column("fabric_receives", sa.Column("qc_config", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
    op.add_column("fabric_receives", sa.Column("qc_setup_status", sa.String(), nullable=False, server_default="draft"))
    op.add_column("fabric_receives", sa.Column("qc_setup_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("fabric_receives", sa.Column("notes", sa.String(), nullable=True))
    op.execute("UPDATE fabric_receives SET received_quantity=fabric_length WHERE received_quantity IS NULL")

    # PLM planning and bulk roll metadata.
    op.add_column("plms", sa.Column("allocated_quantity", sa.Numeric(), nullable=True))
    op.add_column("plm_colors", sa.Column("planned_quantity", sa.Numeric(), nullable=True))
    op.add_column("plm_rolls", sa.Column("marked_width", sa.Numeric(), nullable=True))
    op.add_column("plm_rolls", sa.Column("marked_length", sa.Numeric(), nullable=True))
    op.add_column("plm_rolls", sa.Column("planned_quantity", sa.Numeric(), nullable=True))

    # Reusable supplier defaults can be scoped; final values still live in consignment qc_config.
    op.add_column("fabric_composition_standards", sa.Column("supplier_id", sa.UUID(), nullable=True))
    op.create_foreign_key("fk_comp_standard_supplier", "fabric_composition_standards", "suppliers", ["supplier_id"], ["id"])
    op.add_column("fabric_width_standards", sa.Column("supplier_id", sa.UUID(), nullable=True))
    op.create_foreign_key("fk_width_standard_supplier", "fabric_width_standards", "suppliers", ["supplier_id"], ["id"])
    op.add_column("shade_check_point_definitions", sa.Column("supplier_id", sa.UUID(), nullable=True))
    op.create_foreign_key("fk_shade_point_supplier", "shade_check_point_definitions", "suppliers", ["supplier_id"], ["id"])
    # Old single-column uniqueness blocked supplier-specific definitions.
    # Inspect first instead of swallowing DDL failures: on PostgreSQL a failed DDL
    # statement aborts the whole transaction even if Python catches the exception.
    inspector = inspect(op.get_bind())
    comp_uqs = {c.get("name") for c in inspector.get_unique_constraints("fabric_composition_standards")}
    shade_uqs = {c.get("name") for c in inspector.get_unique_constraints("shade_check_point_definitions")}
    if "fabric_composition_standards_composition_name_key" in comp_uqs:
        op.drop_constraint("fabric_composition_standards_composition_name_key", "fabric_composition_standards", type_="unique")
    if "shade_check_point_definitions_check_key_key" in shade_uqs:
        op.drop_constraint("shade_check_point_definitions_check_key_key", "shade_check_point_definitions", type_="unique")

    # Four-point report calculation snapshots.
    op.add_column("fabric_inspection_reports", sa.Column("measurement_unit_snapshot", sa.String(), nullable=False, server_default="inch"))
    op.add_column("fabric_inspection_reports", sa.Column("qc_setup_version_snapshot", sa.Integer(), nullable=False, server_default="1"))

    # Per-roll master tracks 4-point Fabric Inspection like every other roll module.
    op.add_column("inspection_reports", sa.Column("fabric_inspection_roll_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_inspection_reports_fabric_inspection_roll",
        "inspection_reports",
        "fabric_inspection_rolls",
        ["fabric_inspection_roll_id"],
        ["id"],
    )
    # Backfill existing 4-point results by the relational PLM + roll key.
    op.execute("""
        UPDATE inspection_reports ir
        SET fabric_inspection_roll_id = fi.id
        FROM fabric_inspection_rolls fi
        JOIN fabric_inspection_reports fr ON fr.id = fi.report_id
        WHERE ir.plm_id = fr.plm_id
          AND ir.roll_number = fi.roll_no
          AND ir.fabric_inspection_roll_id IS NULL
    """)

    # Clean historical duplicates before enforcing idempotency at DB level.
    _dedupe_simple("gsm_check_rolls", "report_id", "gsm_report_id")
    _dedupe_simple("moisture_check_rolls", "report_id", "moisture_report_id")
    _dedupe_simple("skew_bowing_rolls", "report_id", "skew_report_id")
    _dedupe_simple("fabric_relaxation_rolls", "report_id", "relaxation_report_id")
    _dedupe_simple("shade_check_rolls", "report_id", "shade_check_report_id", "shade_check_results", "roll_id")
    _dedupe_simple("shrinkage_rolls", "report_id", "shrinkage_report_id", "shrinkage_measurements", "roll_id")
    _dedupe_simple("fabric_correlation_rolls", "report_id", "correlation_report_id", "fabric_correlation_roll_defects", "roll_id")
    _dedupe_simple("color_continuity_rolls", "card_id", "color_continuity_card_id")
    _dedupe_simple("fabric_inspection_rolls", "report_id", "fabric_inspection_roll_id", "fabric_inspection_roll_defects", "roll_id")

    # Planned hierarchy duplicate cleanup. Merge duplicate colors into the oldest
    # color first so existing planned rolls are preserved instead of orphaned.
    color_mapping = """
        WITH ranked AS (
            SELECT id, FIRST_VALUE(id) OVER (
                PARTITION BY plm_id, colour_code ORDER BY created_at ASC, id ASC
            ) AS keep_id,
            ROW_NUMBER() OVER (
                PARTITION BY plm_id, colour_code ORDER BY created_at ASC, id ASC
            ) AS rn
            FROM plm_colors
        )
        SELECT id, keep_id FROM ranked WHERE rn > 1
    """
    # If both duplicate colors already contain the same roll number, keep the
    # roll that belongs to the retained color; PLM-roll rows are planning rows
    # and are not referenced by historical inspection result tables.
    op.execute(f"""
        WITH dup AS ({color_mapping})
        DELETE FROM plm_rolls r USING dup
        WHERE r.plm_color_id=dup.id
          AND EXISTS (
            SELECT 1 FROM plm_rolls kept
            WHERE kept.plm_color_id=dup.keep_id AND kept.roll_no=r.roll_no
          )
    """)
    op.execute(f"""
        WITH dup AS ({color_mapping})
        UPDATE plm_rolls r SET plm_color_id=dup.keep_id
        FROM dup WHERE r.plm_color_id=dup.id
    """)
    op.execute(f"""
        WITH dup AS ({color_mapping})
        DELETE FROM plm_colors c USING dup WHERE c.id=dup.id
    """)

    # Now remove any exact duplicate roll rows that pre-date the constraint.
    op.execute("""
        WITH ranked AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY plm_color_id, roll_no ORDER BY created_at ASC, id ASC
            ) rn
            FROM plm_rolls
        ), dup AS (SELECT id FROM ranked WHERE rn>1)
        DELETE FROM plm_rolls p USING dup WHERE p.id=dup.id
    """)

    # The per-roll master itself must also be idempotent. Keep the newest master row.
    op.execute("""
        WITH ranked AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY plm_id, roll_number ORDER BY submitted_at DESC NULLS LAST, updated_at DESC, created_at DESC, id DESC
            ) rn
            FROM inspection_reports
        ), dup AS (SELECT id FROM ranked WHERE rn>1)
        DELETE FROM inspection_reports ir USING dup WHERE ir.id=dup.id
    """)

    # Factory report is PLM-level and idempotent. Keep newest historical row per PLM.
    op.execute("""
        WITH ranked AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY plm_id ORDER BY updated_at DESC, created_at DESC, id DESC
            ) rn
            FROM factory_reports
        ), dup AS (SELECT id FROM ranked WHERE rn>1)
        DELETE FROM factory_reports fr USING dup WHERE fr.id=dup.id
    """)
    op.create_unique_constraint("uq_factory_report_plm", "factory_reports", ["plm_id"])
    op.create_unique_constraint("uq_inspection_report_plm_roll", "inspection_reports", ["plm_id", "roll_number"])
    op.create_unique_constraint("uq_plm_color_code", "plm_colors", ["plm_id", "colour_code"])
    op.create_unique_constraint("uq_plm_color_roll_no", "plm_rolls", ["plm_color_id", "roll_no"])
    op.create_unique_constraint("uq_gsm_report_roll", "gsm_check_rolls", ["report_id", "roll_no"])
    op.create_unique_constraint("uq_moisture_report_roll", "moisture_check_rolls", ["report_id", "roll_no"])
    op.create_unique_constraint("uq_skew_report_roll", "skew_bowing_rolls", ["report_id", "roll_no"])
    op.create_unique_constraint("uq_relaxation_report_roll", "fabric_relaxation_rolls", ["report_id", "roll_no"])
    op.create_unique_constraint("uq_shade_report_roll", "shade_check_rolls", ["report_id", "roll_no"])
    op.create_unique_constraint("uq_shrinkage_report_roll", "shrinkage_rolls", ["report_id", "roll_no"])
    op.create_unique_constraint("uq_correlation_report_roll", "fabric_correlation_rolls", ["report_id", "roll_no"])
    op.create_unique_constraint("uq_continuity_card_roll", "color_continuity_rolls", ["card_id", "roll_no"])
    op.create_unique_constraint("uq_fabric_inspection_report_roll", "fabric_inspection_rolls", ["report_id", "roll_no"])


def downgrade() -> None:
    for name, table in [
        ("uq_fabric_inspection_report_roll", "fabric_inspection_rolls"),
        ("uq_inspection_report_plm_roll", "inspection_reports"),
        ("uq_factory_report_plm", "factory_reports"),
        ("uq_continuity_card_roll", "color_continuity_rolls"),
        ("uq_correlation_report_roll", "fabric_correlation_rolls"),
        ("uq_shrinkage_report_roll", "shrinkage_rolls"),
        ("uq_shade_report_roll", "shade_check_rolls"),
        ("uq_relaxation_report_roll", "fabric_relaxation_rolls"),
        ("uq_skew_report_roll", "skew_bowing_rolls"),
        ("uq_moisture_report_roll", "moisture_check_rolls"),
        ("uq_gsm_report_roll", "gsm_check_rolls"),
        ("uq_plm_color_roll_no", "plm_rolls"),
        ("uq_plm_color_code", "plm_colors"),
    ]:
        op.drop_constraint(name, table, type_="unique")
    op.drop_constraint("fk_inspection_reports_fabric_inspection_roll", "inspection_reports", type_="foreignkey")
    op.drop_column("inspection_reports", "fabric_inspection_roll_id")
    op.drop_column("fabric_inspection_reports", "qc_setup_version_snapshot")
    op.drop_column("fabric_inspection_reports", "measurement_unit_snapshot")
    op.drop_constraint("fk_shade_point_supplier", "shade_check_point_definitions", type_="foreignkey")
    op.drop_column("shade_check_point_definitions", "supplier_id")
    op.drop_constraint("fk_width_standard_supplier", "fabric_width_standards", type_="foreignkey")
    op.drop_column("fabric_width_standards", "supplier_id")
    op.drop_constraint("fk_comp_standard_supplier", "fabric_composition_standards", type_="foreignkey")
    op.drop_column("fabric_composition_standards", "supplier_id")
    op.drop_column("plm_rolls", "planned_quantity")
    op.drop_column("plm_rolls", "marked_length")
    op.drop_column("plm_rolls", "marked_width")
    op.drop_column("plm_colors", "planned_quantity")
    op.drop_column("plms", "allocated_quantity")
    for col in ["notes", "qc_setup_version", "qc_setup_status", "qc_config", "measurement_unit", "fabric_composition", "fabric_type", "quantity_unit", "received_quantity"]:
        op.drop_column("fabric_receives", col)
