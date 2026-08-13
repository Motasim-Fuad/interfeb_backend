from copy import deepcopy
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.features.admin_config import models


def _plain_point(row: models.ShadeCheckPointDefinition) -> Dict[str, Any]:
    check_type = row.check_type.value if hasattr(row.check_type, "value") else str(row.check_type)
    return {
        "id": str(row.id),
        "check_key": row.check_key,
        "label": row.label,
        "check_type": check_type,
        "sort_order": row.sort_order,
    }


def supplier_shade_points(db: Session, supplier_id: UUID) -> List[Dict[str, Any]]:
    """Return only this supplier's active Shade Check layout.

    Supplier QC Profile is the single configuration source. We deliberately do not
    mix global or another supplier's rows, which prevents Bosundhara/Jomuna columns
    from ever appearing in the same finalized consignment.
    """
    rows = db.query(models.ShadeCheckPointDefinition).filter(
        models.ShadeCheckPointDefinition.supplier_id == supplier_id,
        models.ShadeCheckPointDefinition.is_active == True,  # noqa: E712
    ).order_by(
        models.ShadeCheckPointDefinition.sort_order,
        models.ShadeCheckPointDefinition.created_at,
    ).all()
    return [_plain_point(x) for x in rows]


def get_supplier_qc_config(db: Session, supplier_id: UUID) -> Dict[str, Any]:
    """Supplier QC Profile is the single default source for scalar QC planning values.

    Shade Check is also supplier-owned, but stored as normalized rows so each point can
    be enabled/disabled cleanly. Fabric Receive receives one combined payload.
    """
    profile = db.query(models.SupplierQcProfile).filter(
        models.SupplierQcProfile.supplier_id == supplier_id,
        models.SupplierQcProfile.is_active == True,  # noqa: E712
    ).first()
    config: Dict[str, Any] = deepcopy(profile.qc_config or {}) if profile else {}
    config.setdefault("shade_check", {})["points"] = supplier_shade_points(db, supplier_id)
    return config


def supplier_defaults_payload(db: Session, supplier_id: UUID) -> Optional[Dict[str, Any]]:
    supplier = db.query(models.Supplier).get(supplier_id)
    if not supplier or not supplier.is_active:
        return None
    return {
        "supplier_id": str(supplier.id),
        "supplier_name": supplier.name,
        "measurement_unit": supplier.default_measurement_unit,
        "qc_config": get_supplier_qc_config(db, supplier.id),
    }


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result
