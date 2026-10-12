"""v9: index the per-module roll links on inspection_reports (roll delete clears them)

Revision ID: f4b8c6d2e315
Revises: e3a7b5c1d204
"""
from alembic import op

revision = "f4b8c6d2e315"
down_revision = "e3a7b5c1d204"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_inspection_reports_gsm_report_id", "inspection_reports", ["gsm_report_id"])
    op.create_index("ix_inspection_reports_moisture_report_id", "inspection_reports", ["moisture_report_id"])
    op.create_index("ix_inspection_reports_skew_report_id", "inspection_reports", ["skew_report_id"])
    op.create_index("ix_inspection_reports_relaxation_report_id", "inspection_reports", ["relaxation_report_id"])
    op.create_index("ix_inspection_reports_factory_report_id", "inspection_reports", ["factory_report_id"])
    op.create_index("ix_inspection_reports_shade_check_report_id", "inspection_reports", ["shade_check_report_id"])
    op.create_index("ix_inspection_reports_correlation_report_id", "inspection_reports", ["correlation_report_id"])
    op.create_index("ix_inspection_reports_shrinkage_report_id", "inspection_reports", ["shrinkage_report_id"])
    op.create_index("ix_inspection_reports_color_continuity_card_id", "inspection_reports", ["color_continuity_card_id"])
    op.create_index("ix_inspection_reports_fabric_inspection_roll_id", "inspection_reports", ["fabric_inspection_roll_id"])


def downgrade():
    op.drop_index("ix_inspection_reports_gsm_report_id", table_name="inspection_reports")
    op.drop_index("ix_inspection_reports_moisture_report_id", table_name="inspection_reports")
    op.drop_index("ix_inspection_reports_skew_report_id", table_name="inspection_reports")
    op.drop_index("ix_inspection_reports_relaxation_report_id", table_name="inspection_reports")
    op.drop_index("ix_inspection_reports_factory_report_id", table_name="inspection_reports")
    op.drop_index("ix_inspection_reports_shade_check_report_id", table_name="inspection_reports")
    op.drop_index("ix_inspection_reports_correlation_report_id", table_name="inspection_reports")
    op.drop_index("ix_inspection_reports_shrinkage_report_id", table_name="inspection_reports")
    op.drop_index("ix_inspection_reports_color_continuity_card_id", table_name="inspection_reports")
    op.drop_index("ix_inspection_reports_fabric_inspection_roll_id", table_name="inspection_reports")
