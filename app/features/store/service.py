from typing import Any, Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.features.plm.models import Plm
from app.features.store.models import FabricReceive


def get_receive_for_plm(db: Session, plm: Plm | str) -> Optional[FabricReceive]:
    if isinstance(plm, str):
        plm = db.query(Plm).get(plm)
    if not plm or not plm.fabric_receive_id:
        return None
    return db.query(FabricReceive).get(plm.fabric_receive_id)


def get_qc_value(context: Any, module: str, key: str, default: Any = None) -> Any:
    """Read a QC value from a PLM snapshot or legacy FabricReceive context."""
    if not context or not getattr(context, "qc_config", None):
        return default
    module_config = context.qc_config.get(module) or {}
    value = module_config.get(key, default)
    return value


def get_float_qc_value(context: Any, module: str, key: str, default=None):
    value = get_qc_value(context, module, key, default)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def resolve_qc_float(db: Session, plm: Plm, module: str, key: str, *, legacy_standard_module: str | None = None, default=None):
    """
    Final standard resolution for inspections:
      1) immutable PLM snapshot of the finalized consignment QC setup
      2) linked FabricReceive only as backwards compatibility for old PLM rows
      3) legacy admin-config standard as an old-data fallback
      4) explicit default
    """
    # New PLMs carry an immutable snapshot of the finalized consignment QC setup.
    # This is the authoritative source for every inspection belonging to that PLM.
    value = get_float_qc_value(plm, module, key, None)
    if value is not None:
        return value, None

    # Backwards-compatible fallback for old rows created before PLM snapshots existed.
    receive = get_receive_for_plm(db, plm)
    value = get_float_qc_value(receive, module, key, None)
    if value is not None:
        return value, None
    if legacy_standard_module:
        from app.features.admin_config.routes import get_effective_standard
        row = get_effective_standard(db, legacy_standard_module, plm.buyer_id, plm.supplier_id)
        if row:
            return float(row.standard_value), row.id
    return default, None


def require_qc_float(db: Session, plm: Plm, module: str, key: str, *, legacy_standard_module: str | None = None) -> tuple[float, Any]:
    """Resolve a finalized planning value or stop the inspection.

    The factory app is intentionally not allowed to invent/fallback fixed standards.
    If a PLM somehow reaches inspection without its consignment setup, fail loudly and
    send the admin back to Dashboard instead of calculating with a hidden default.
    """
    value, source_id = resolve_qc_float(
        db, plm, module, key, legacy_standard_module=legacy_standard_module, default=None
    )
    if value is None:
        raise HTTPException(
            status_code=422,
            detail=f"Missing Dashboard QC setup: {module}.{key}. Update/finalize the Fabric Receive first.",
        )
    return float(value), source_id
