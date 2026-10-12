"""v8: defect location/photo, roll disposition, offline sync stamp, audit log, access-code lookup, indexes

Revision ID: b4f1c8e2d930
Revises: d9e4a1b72c30
Create Date: 2026-10-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b4f1c8e2d930"
down_revision = "d9e4a1b72c30"
branch_labels = None
depends_on = None

_INDEXES = [
    ("ix_plms_supplier_id", "plms", "supplier_id"),
    ("ix_plms_buyer_id", "plms", "buyer_id"),
    ("ix_plms_fabric_receive_id", "plms", "fabric_receive_id"),
    ("ix_plms_plm_date", "plms", "plm_date"),
    ("ix_plms_created_at", "plms", "created_at"),
    ("ix_fabric_receives_supplier_id", "fabric_receives", "supplier_id"),
    ("ix_fabric_receives_buyer_id", "fabric_receives", "buyer_id"),
    ("ix_fabric_receives_received_at", "fabric_receives", "received_at"),
    ("ix_fabric_receives_created_at", "fabric_receives", "created_at"),
    ("ix_fabric_inspection_reports_inspection_date", "fabric_inspection_reports", "inspection_date"),
    ("ix_fabric_inspection_rolls_created_at", "fabric_inspection_rolls", "created_at"),
    ("ix_fabric_inspection_rolls_is_pass", "fabric_inspection_rolls", "is_pass"),
    ("ix_fabric_inspection_roll_defects_defect_type_id", "fabric_inspection_roll_defects", "defect_type_id"),
    ("ix_inspection_reports_submitted_at", "inspection_reports", "submitted_at"),
    ("ix_inspection_schedules_status", "inspection_schedules", "status"),
    ("ix_inspection_schedules_scheduled_date", "inspection_schedules", "scheduled_date"),
    ("ix_plm_rolls_roll_no", "plm_rolls", "roll_no"),
]


def upgrade():
    op.add_column("fabric_inspection_roll_defects", sa.Column("position_yard", sa.Numeric(), nullable=True))
    op.add_column("fabric_inspection_roll_defects", sa.Column("side", sa.String(), nullable=True))
    op.add_column("fabric_inspection_roll_defects", sa.Column("photo_url", sa.String(), nullable=True))
    op.add_column("fabric_inspection_roll_defects", sa.Column("recorded_at", sa.DateTime(), nullable=True))

    op.add_column("fabric_inspection_rolls", sa.Column("disposition", sa.String(), nullable=False, server_default="pending"))
    op.add_column("fabric_inspection_rolls", sa.Column("disposition_note", sa.String(), nullable=True))
    op.add_column("fabric_inspection_rolls", sa.Column("shade_band", sa.String(), nullable=True))
    op.add_column("fabric_inspection_rolls", sa.Column("inspector_name", sa.String(), nullable=True))
    op.add_column("fabric_inspection_rolls", sa.Column("inspected_at", sa.DateTime(), nullable=True))
    op.add_column("fabric_inspection_rolls", sa.Column("client_updated_at", sa.DateTime(), nullable=True))
    op.create_index("ix_fabric_inspection_rolls_disposition", "fabric_inspection_rolls", ["disposition"])
    op.create_index("ix_fabric_inspection_rolls_inspected_at", "fabric_inspection_rolls", ["inspected_at"])

    op.add_column("access_codes", sa.Column("code_lookup", sa.String(), nullable=True))
    op.create_index("ix_access_codes_code_lookup", "access_codes", ["code_lookup"], unique=True)

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("actor_role", sa.String(), nullable=True),
        sa.Column("actor_label", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("plm_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("summary", sa.String(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
    )
    op.create_index("ix_audit_logs_id", "audit_logs", ["id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index("ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"])
    op.create_index("ix_audit_logs_plm_id", "audit_logs", ["plm_id"])

    for name, table, column in _INDEXES:
        op.execute(f'CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})')
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_attachments_owner_lookup ON attachments (owner_type, owner_id, created_at)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_attachments_owner_lookup")
    for name, _, _ in reversed(_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {name}")

    op.drop_index("ix_audit_logs_plm_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_entity", table_name="audit_logs")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_id", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_access_codes_code_lookup", table_name="access_codes")
    op.drop_column("access_codes", "code_lookup")

    op.drop_index("ix_fabric_inspection_rolls_inspected_at", table_name="fabric_inspection_rolls")
    op.drop_index("ix_fabric_inspection_rolls_disposition", table_name="fabric_inspection_rolls")
    for column in ("client_updated_at", "inspected_at", "inspector_name", "shade_band", "disposition_note", "disposition"):
        op.drop_column("fabric_inspection_rolls", column)
    for column in ("recorded_at", "photo_url", "side", "position_yard"):
        op.drop_column("fabric_inspection_roll_defects", column)
