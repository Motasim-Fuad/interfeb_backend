"""
All admin-configurable lookup tables live here. Every one of these follows the same
create / list / update / deactivate shape - built with a tiny generic helper below so
adding a 7th, 8th... config table later is copy-paste-and-rename, not new logic.
"""
from typing import List, Type, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_admin
from app.features.admin_config import models, schemas
from app.features.admin_config.service import supplier_defaults_payload, supplier_shade_points

router = APIRouter(prefix="/admin-config", tags=["Admin Config (dashboard-editable)"])


def _normalize_supplier_profile_config(raw: dict) -> dict:
    """Validate and normalize the supplier-owned QC profile JSON.

    Moisture and Skew/Bowing are multi-standard sections in the real client forms, so
    they are stored as lists inside the one Supplier QC Profile instead of separate
    dashboard pages. Legacy scalar fields are removed to prevent two sources of truth.
    """
    config = dict(raw or {})

    moisture = dict(config.get("moisture") or {})
    standards = moisture.get("composition_standards") or []
    if not isinstance(standards, list):
        raise HTTPException(status_code=422, detail="moisture.composition_standards must be a list")
    clean_moisture = []
    seen = set()
    for row in standards:
        if not isinstance(row, dict):
            raise HTTPException(status_code=422, detail="Each moisture composition standard must be an object")
        name = str(row.get("composition_name") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="Moisture composition name cannot be blank")
        key = name.casefold()
        if key in seen:
            raise HTTPException(status_code=422, detail=f"Duplicate moisture composition standard: {name}")
        seen.add(key)
        try:
            value = float(row.get("acceptance_percentage"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"Invalid moisture acceptance for {name}")
        if value < 0:
            raise HTTPException(status_code=422, detail=f"Moisture acceptance cannot be negative: {name}")
        clean_moisture.append({"composition_name": name, "acceptance_percentage": value})
    moisture.pop("acceptance_percentage", None)
    moisture.pop("standard_label", None)
    moisture["composition_standards"] = clean_moisture
    config["moisture"] = moisture

    skew = dict(config.get("skew_bowing") or {})
    width_standards = skew.get("width_standards") or []
    if not isinstance(width_standards, list):
        raise HTTPException(status_code=422, detail="skew_bowing.width_standards must be a list")
    clean_width = []
    seen = set()
    for row in width_standards:
        if not isinstance(row, dict):
            raise HTTPException(status_code=422, detail="Each Skew/Bowing width standard must be an object")
        label = str(row.get("width_label") or "").strip()
        if not label:
            raise HTTPException(status_code=422, detail="Skew/Bowing width label cannot be blank")
        key = label.casefold()
        if key in seen:
            raise HTTPException(status_code=422, detail=f"Duplicate Skew/Bowing width standard: {label}")
        seen.add(key)
        try:
            solid = float(row.get("solid_standard"))
            stripe = float(row.get("stripe_standard"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"Invalid Skew/Bowing standard for {label}")
        if solid < 0 or stripe < 0:
            raise HTTPException(status_code=422, detail=f"Skew/Bowing standards cannot be negative: {label}")
        clean_width.append({"width_label": label, "solid_standard": solid, "stripe_standard": stripe})
    for legacy in ("standard_point", "width_label", "reference_fabric_length", "fabric_width_type", "is_stripe"):
        skew.pop(legacy, None)
    skew["width_standards"] = clean_width
    config["skew_bowing"] = skew

    relaxation = dict(config.get("relaxation") or {})
    relaxation.pop("accepted_standard", None)
    config["relaxation"] = relaxation

    shrinkage = dict(config.get("shrinkage") or {})
    shrinkage.pop("accepted_standard", None)
    if shrinkage:
        config["shrinkage"] = shrinkage
    else:
        config.pop("shrinkage", None)

    correlation = dict(config.get("correlation") or {})
    correlation.pop("tolerance", None)
    config["correlation"] = correlation
    config.pop("factory_report", None)
    return config


def register_crud(prefix: str, model: Type, schema_in, schema_out):
    sub = APIRouter(prefix=f"/{prefix}", tags=["Admin Config (dashboard-editable)"])

    @sub.post("", response_model=schema_out)
    def create(payload: schema_in, db: Session = Depends(get_db), _=Depends(require_admin())):
        row = model(**payload.model_dump())
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    @sub.get("", response_model=List[schema_out])
    def list_all(active_only: bool = False, supplier_id: Optional[UUID] = None, db: Session = Depends(get_db)):
        q = db.query(model)
        if active_only:
            q = q.filter(model.is_active == True)  # noqa: E712
        if supplier_id is not None and hasattr(model, "supplier_id"):
            q = q.filter(model.supplier_id == supplier_id)
        return q.order_by(getattr(model, "sort_order", model.created_at)).all()

    @sub.put("/{item_id}", response_model=schema_out)
    def update(item_id: UUID, payload: schema_in, db: Session = Depends(get_db), _=Depends(require_admin())):
        row = db.query(model).get(item_id)
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        for k, v in payload.model_dump().items():
            setattr(row, k, v)
        db.commit()
        db.refresh(row)
        return row

    @sub.patch("/{item_id}/deactivate", response_model=schema_out)
    def deactivate(item_id: UUID, db: Session = Depends(get_db), _=Depends(require_admin())):
        row = db.query(model).get(item_id)
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        row.is_active = False
        db.commit()
        db.refresh(row)
        return row

    @sub.delete("/{item_id}", status_code=204)
    def delete(item_id: UUID, db: Session = Depends(get_db), _=Depends(require_admin())):
        # Soft delete preserves historical FK references/snapshots while immediately
        # removing the item from app dropdowns that request active_only=true.
        row = db.query(model).get(item_id)
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        row.is_active = False
        db.commit()

    return sub


router.include_router(register_crud("buyers", models.Buyer, schemas.BuyerIn, schemas.BuyerOut))
router.include_router(register_crud("suppliers", models.Supplier, schemas.SupplierIn, schemas.SupplierOut))
router.include_router(register_crud(
    "supplier-qc-profiles", models.SupplierQcProfile,
    schemas.SupplierQcProfileIn, schemas.SupplierQcProfileOut,
))
router.include_router(register_crud("defect-types", models.DefectType, schemas.DefectTypeIn, schemas.DefectTypeOut))
router.include_router(register_crud(
    "defect-range-configs", models.DefectRangeConfig,
    schemas.DefectRangeConfigIn, schemas.DefectRangeConfigOut,
))
router.include_router(register_crud(
    "acceptance-standards", models.AcceptanceStandard,
    schemas.AcceptanceStandardIn, schemas.AcceptanceStandardOut,
))
router.include_router(register_crud(
    "fabric-composition-standards", models.FabricCompositionStandard,
    schemas.FabricCompositionStandardIn, schemas.FabricCompositionStandardOut,
))
router.include_router(register_crud(
    "fabric-width-standards", models.FabricWidthStandard,
    schemas.FabricWidthStandardIn, schemas.FabricWidthStandardOut,
))
router.include_router(register_crud(
    "shade-check-point-definitions", models.ShadeCheckPointDefinition,
    schemas.ShadeCheckPointDefinitionIn, schemas.ShadeCheckPointDefinitionOut,
))


@router.put("/suppliers/{supplier_id}/qc-profile", response_model=schemas.SupplierQcDefaultsOut)
def save_supplier_qc_profile(
    supplier_id: UUID,
    payload: schemas.SupplierQcProfileSaveIn,
    db: Session = Depends(get_db),
    _=Depends(require_admin()),
):
    """Atomically save one supplier's complete QC profile, including Shade Check.

    The dashboard calls one endpoint so a scalar profile cannot save successfully while
    Shade Check fails in a second request. Fabric Receive therefore always receives one
    consistent supplier snapshot.
    """
    supplier = db.query(models.Supplier).get(supplier_id)
    if not supplier or not supplier.is_active:
        raise HTTPException(status_code=404, detail="Active supplier not found")

    profile = db.query(models.SupplierQcProfile).filter(
        models.SupplierQcProfile.supplier_id == supplier_id
    ).first()
    if profile is None:
        profile = models.SupplierQcProfile(supplier_id=supplier_id, qc_config={}, is_active=payload.is_active)
        db.add(profile)
    else:
        profile.is_active = payload.is_active

    existing_rows = db.query(models.ShadeCheckPointDefinition).filter(
        models.ShadeCheckPointDefinition.supplier_id == supplier_id
    ).all()
    by_key = {row.check_key: row for row in existing_rows}
    incoming_keys = set()
    for point in payload.shade_points:
        key = point.check_key.strip()
        if not key:
            raise HTTPException(status_code=422, detail="Shade Check key cannot be blank")
        incoming_keys.add(key)
        row = by_key.get(key)
        values = point.model_dump()
        values["check_key"] = key
        if row is None:
            row = models.ShadeCheckPointDefinition(supplier_id=supplier_id, **values)
            db.add(row)
        else:
            for field, value in values.items():
                setattr(row, field, value)

    # Anything that used to belong to this supplier but is no longer in the profile is
    # deactivated instead of deleted so historical FK references remain valid.
    for row in existing_rows:
        if row.check_key not in incoming_keys:
            row.is_active = False

    db.flush()
    config = _normalize_supplier_profile_config(dict(payload.qc_config or {}))
    shade_section = dict(config.get("shade_check") or {})
    shade_section["points"] = supplier_shade_points(db, supplier_id)
    config["shade_check"] = shade_section
    profile.qc_config = config

    db.commit()
    result = supplier_defaults_payload(db, supplier_id)
    if not result:
        raise HTTPException(status_code=404, detail="Active supplier not found")
    return result


@router.get("/suppliers/{supplier_id}/qc-defaults", response_model=schemas.SupplierQcDefaultsOut)
def get_supplier_qc_defaults(supplier_id: UUID, db: Session = Depends(get_db)):
    payload = supplier_defaults_payload(db, supplier_id)
    if not payload:
        raise HTTPException(status_code=404, detail="Active supplier not found")
    return payload


def get_effective_standard(db: Session, module: str, buyer_id: UUID = None, supplier_id: UUID = None):
    """
    Resolution order: supplier-specific standard wins, then buyer-specific, then global default.
    Used by every inspection module when it creates a report.
    """
    if supplier_id:
        row = (
            db.query(models.AcceptanceStandard)
            .filter(
                models.AcceptanceStandard.module == module,
                models.AcceptanceStandard.supplier_id == supplier_id,
                models.AcceptanceStandard.is_active == True,  # noqa: E712
            )
            .first()
        )
        if row:
            return row

    if buyer_id:
        row = (
            db.query(models.AcceptanceStandard)
            .filter(
                models.AcceptanceStandard.module == module,
                models.AcceptanceStandard.buyer_id == buyer_id,
                models.AcceptanceStandard.supplier_id.is_(None),
                models.AcceptanceStandard.is_active == True,  # noqa: E712
            )
            .first()
        )
        if row:
            return row

    return (
        db.query(models.AcceptanceStandard)
        .filter(
            models.AcceptanceStandard.module == module,
            models.AcceptanceStandard.buyer_id.is_(None),
            models.AcceptanceStandard.supplier_id.is_(None),
            models.AcceptanceStandard.is_active == True,  # noqa: E712
        )
        .first()
    )
