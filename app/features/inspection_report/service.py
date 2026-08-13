from datetime import datetime
from uuid import UUID
from typing import Optional

from sqlalchemy.orm import Session

from app.features.inspection_report.models import InspectionReport

VALID_MODULE_FIELDS = {
    "gsm_report_id", "moisture_report_id", "skew_report_id", "relaxation_report_id",
    "factory_report_id", "shade_check_report_id", "correlation_report_id",
    "shrinkage_report_id", "color_continuity_card_id", "fabric_inspection_roll_id",
}


def upsert_inspection_report(
    db: Session,
    plm_id: UUID,
    roll_number: str,
    color_name: Optional[str] = None,
    module_field: Optional[str] = None,
    module_row_id: Optional[UUID] = None,
):
    """
    Call this from every module's "add roll" endpoint after it saves its own roll row.
    Creates the per-roll master record on first submission, updates the relevant
    module_field on every subsequent submission for that same (plm_id, roll_number).
    """
    row = (
        db.query(InspectionReport)
        .filter(InspectionReport.plm_id == plm_id, InspectionReport.roll_number == roll_number)
        .first()
    )
    if not row:
        row = InspectionReport(plm_id=plm_id, roll_number=roll_number, color_name=color_name)
        db.add(row)
        db.flush()

    if module_field:
        if module_field not in VALID_MODULE_FIELDS:
            raise ValueError(f"Unknown module_field: {module_field}")
        if module_row_id is not None:
            setattr(row, module_field, module_row_id)

    row.submitted_at = datetime.utcnow()
    db.flush()
    return row
