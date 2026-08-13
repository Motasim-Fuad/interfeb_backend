"""snapshot finalized consignment qc context on plm

Revision ID: d013f09a2c77
Revises: c912d480ee31
Create Date: 2026-08-12
"""
from alembic import op
import sqlalchemy as sa

revision = "d013f09a2c77"
down_revision = "c912d480ee31"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("plms", sa.Column("received_quantity", sa.Numeric(), nullable=True))
    op.add_column("plms", sa.Column("quantity_unit", sa.String(), nullable=False, server_default="yds"))
    op.add_column("plms", sa.Column("received_at", sa.Date(), nullable=True))
    op.add_column("plms", sa.Column("qc_config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("plms", sa.Column("qc_setup_version", sa.Integer(), nullable=False, server_default="1"))

    # Preserve existing deployments too. Fresh databases also pass through this migration.
    op.execute("""
        UPDATE plms p
        SET received_quantity = fr.received_quantity,
            quantity_unit = fr.quantity_unit,
            received_at = fr.received_at,
            qc_config = fr.qc_config,
            qc_setup_version = fr.qc_setup_version
        FROM fabric_receives fr
        WHERE p.fabric_receive_id = fr.id
    """)
    op.alter_column("plms", "quantity_unit", server_default=None)
    op.alter_column("plms", "qc_config", server_default=None)
    op.alter_column("plms", "qc_setup_version", server_default=None)


def downgrade():
    op.drop_column("plms", "qc_setup_version")
    op.drop_column("plms", "qc_config")
    op.drop_column("plms", "received_at")
    op.drop_column("plms", "quantity_unit")
    op.drop_column("plms", "received_quantity")
