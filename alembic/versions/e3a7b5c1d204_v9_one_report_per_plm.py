"""v9: one inspection report per PLM per module (offline-sync race guard)

Revision ID: e3a7b5c1d204
Revises: d8c2f4a6b913
"""
from alembic import op

revision = "e3a7b5c1d204"
down_revision = "d8c2f4a6b913"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint("uq_fabric_inspection_reports_plm", "fabric_inspection_reports", ["plm_id"])
    op.create_unique_constraint("uq_gsm_check_reports_plm", "gsm_check_reports", ["plm_id"])
    op.create_unique_constraint("uq_moisture_check_reports_plm", "moisture_check_reports", ["plm_id"])
    op.create_unique_constraint("uq_skew_bowing_reports_plm", "skew_bowing_reports", ["plm_id"])
    op.create_unique_constraint("uq_fabric_relaxation_reports_plm", "fabric_relaxation_reports", ["plm_id"])
    op.create_unique_constraint("uq_shade_check_reports_plm", "shade_check_reports", ["plm_id"])
    op.create_unique_constraint("uq_shrinkage_pattern_reports_plm", "shrinkage_pattern_reports", ["plm_id"])
    op.create_unique_constraint("uq_color_continuity_cards_plm", "color_continuity_cards", ["plm_id"])
    op.create_unique_constraint("uq_fabric_correlation_reports_plm", "fabric_correlation_reports", ["plm_id"])


def downgrade():
    op.drop_constraint("uq_fabric_inspection_reports_plm", "fabric_inspection_reports", type_="unique")
    op.drop_constraint("uq_gsm_check_reports_plm", "gsm_check_reports", type_="unique")
    op.drop_constraint("uq_moisture_check_reports_plm", "moisture_check_reports", type_="unique")
    op.drop_constraint("uq_skew_bowing_reports_plm", "skew_bowing_reports", type_="unique")
    op.drop_constraint("uq_fabric_relaxation_reports_plm", "fabric_relaxation_reports", type_="unique")
    op.drop_constraint("uq_shade_check_reports_plm", "shade_check_reports", type_="unique")
    op.drop_constraint("uq_shrinkage_pattern_reports_plm", "shrinkage_pattern_reports", type_="unique")
    op.drop_constraint("uq_color_continuity_cards_plm", "color_continuity_cards", type_="unique")
    op.drop_constraint("uq_fabric_correlation_reports_plm", "fabric_correlation_reports", type_="unique")
