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
    points = supplier_shade_points(db, supplier_id)
    config.setdefault("shade_check", {})["points"] = points or ensure_default_shade_points(db)
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


DEFAULT_SHADE_POINTS = (
    ("top_to_middle", "Top to Middle Check", "length", 1),
    ("middle_to_bottom", "Middle to Bottom Check", "length", 2),
    ("bottom_to_top", "Bottom to Top Check", "length", 3),
    ("width_4_to_1", "4 to 1 Check", "width", 4),
    ("width_1_to_3", "1 to 3 Check", "width", 5),
    ("width_3_to_5", "3 to 5 Check", "width", 6),
    ("width_5_to_2", "5 to 2 Check", "width", 7),
)


def ensure_default_shade_points(db: Session) -> List[Dict[str, Any]]:
    """Global Shade Check layout used by the app when the dashboard no longer configures points."""
    rows = db.query(models.ShadeCheckPointDefinition).filter(
        models.ShadeCheckPointDefinition.supplier_id.is_(None),
        models.ShadeCheckPointDefinition.is_active == True,  # noqa: E712
    ).all()
    by_key = {row.check_key: row for row in rows}
    created = False
    for key, label, check_type, order in DEFAULT_SHADE_POINTS:
        if key in by_key:
            continue
        db.add(models.ShadeCheckPointDefinition(
            supplier_id=None,
            check_key=key,
            label=label,
            check_type=check_type,
            sort_order=order,
            is_active=True,
        ))
        created = True
    if created:
        db.flush()
        rows = db.query(models.ShadeCheckPointDefinition).filter(
            models.ShadeCheckPointDefinition.supplier_id.is_(None),
            models.ShadeCheckPointDefinition.is_active == True,  # noqa: E712
        ).all()
    rows = sorted(rows, key=lambda row: (row.sort_order or 0, str(row.created_at)))
    return [_plain_point(row) for row in rows]


def parse_optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        import re
        match = re.search(r"[-+]?\d*\.?\d+", str(value))
        return float(match.group()) if match else None


def material_snapshot(row: models.Material) -> Dict[str, Any]:
    fields = (
        "fabric_name", "dyeing_method", "primary_fiber", "fabric_code",
        "fiber_type_1", "fiber_1_pct", "fiber_type_2", "fiber_2_pct", "fiber_type_3", "fiber_3_pct",
        "accepted_moisture",
        "weave_knit_type", "warp_count", "weft_count", "epi", "ppi",
        "gsm_expected", "gsm_tolerance", "fabric_width_expected", "width_tolerance", "fabric_length_per_roll",
        "shade_color_name", "shade_code", "finish", "handfeel", "surface_defect_tolerance", "pattern_design",
    )
    data = {key: getattr(row, key) for key in fields}
    data["id"] = str(row.id)
    data["display_name"] = row.display_name
    data["composition_label"] = row.composition_label
    return data


def qc_config_with_live_material(plm: Any) -> Dict[str, Any]:
    """GSM / moisture / skew numbers always come from the current Material row.

    Consignment 4-point QC stays frozen on the PLM. Material construction numbers
    are master data — dashboard 125 must show in the app without creating a new PLM.
    """
    qc = deepcopy(getattr(plm, "qc_config", None) or {})
    material = getattr(plm, "material", None)
    if material is None:
        return qc
    return apply_material_to_qc(qc, material)


def apply_live_material(plm: Any) -> Any:
    material = getattr(plm, "material", None)
    if material is None:
        return plm
    plm.qc_config = apply_material_to_qc(plm.qc_config or {}, material)
    plm.material_snapshot = material_snapshot(material)
    if material.composition_label:
        plm.fabric_composition = material.composition_label
    if material.fabric_name:
        plm.fabric_type = material.fabric_name
    return plm


def push_material_standards(db: Session, material: models.Material) -> None:
    """Write current Material numbers onto every PLM (and open reports) that uses it."""
    from app.features.plm.models import Plm
    from app.features.gsm.models import GsmCheckReport
    from app.features.moisture.models import MoistureCheckReport
    from app.features.skew_bowing.models import SkewBowingReport

    snap = material_snapshot(material)
    std_gsm = parse_optional_float(material.gsm_expected)
    gsm_tol = parse_optional_float(material.gsm_tolerance)
    moisture = parse_optional_float(material.accepted_moisture)
    skew_std = parse_optional_float(material.width_tolerance)
    width_label = (material.fabric_width_expected or "").strip()

    plms = db.query(Plm).filter(Plm.material_id == material.id).all()
    for plm in plms:
        plm.material_snapshot = snap
        plm.qc_config = apply_material_to_qc(plm.qc_config or {}, material)
        if material.composition_label:
            plm.fabric_composition = material.composition_label
        if material.fabric_name:
            plm.fabric_type = material.fabric_name
        if std_gsm is not None or gsm_tol is not None:
            for report in db.query(GsmCheckReport).filter(GsmCheckReport.plm_id == plm.id).all():
                if std_gsm is not None:
                    report.standard_gsm = std_gsm
                if gsm_tol is not None:
                    report.tolerance_snapshot = gsm_tol
        if moisture is not None:
            for report in db.query(MoistureCheckReport).filter(MoistureCheckReport.plm_id == plm.id).all():
                report.acceptance_snapshot = moisture
        if skew_std is not None or width_label:
            for report in db.query(SkewBowingReport).filter(SkewBowingReport.plm_id == plm.id).all():
                if skew_std is not None:
                    report.standard_point_snapshot = skew_std
                if width_label:
                    report.fabric_width_label_snapshot = width_label


def apply_material_to_qc(qc: Dict[str, Any], material: models.Material) -> Dict[str, Any]:
    """Copy construction/appearance numbers from Materials into the PLM qc_config snapshot.

    Inspection modules still read gsm/moisture/skew from qc_config. Dashboard no longer
    collects those modules on Supplier QC Profile / consignment setup.
    """
    result = deepcopy(qc or {})

    gsm = dict(result.get("gsm") or {})
    standard_gsm = parse_optional_float(material.gsm_expected)
    tolerance = parse_optional_float(material.gsm_tolerance)
    if standard_gsm is not None:
        gsm["standard_gsm"] = standard_gsm
    if tolerance is not None:
        gsm["tolerance_percent"] = tolerance
    result["gsm"] = gsm

    moisture = dict(result.get("moisture") or {})
    if material.composition_label:
        moisture["standard_label"] = material.composition_label
    accepted = parse_optional_float(material.accepted_moisture)
    if accepted is not None:
        moisture["acceptance_percentage"] = accepted
    result["moisture"] = moisture

    skew = dict(result.get("skew_bowing") or {})
    width_label = (material.fabric_width_expected or "").strip()
    if width_label:
        skew["width_label"] = width_label
    standard_point = parse_optional_float(material.width_tolerance)
    if standard_point is not None:
        skew["standard_point"] = standard_point
    result["skew_bowing"] = skew

    relaxation = dict(result.get("relaxation") or {})
    if relaxation.get("relaxation_hours") in (None, ""):
        relaxation["relaxation_hours"] = 12
    result["relaxation"] = relaxation
    return result


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result
