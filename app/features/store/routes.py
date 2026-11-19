from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, require_admin
from app.core.list_query import apply_text_search, apply_year_month, paginate
from app.features.audit.service import record_audit, stamp_creator
from app.features.store import models, schemas
from app.features.admin_config.models import Supplier
from app.features.admin_config.service import get_supplier_qc_config, deep_merge

router = APIRouter(prefix="/store/fabric-receives", tags=["Store - Fabric Receive"])


_FINAL_QC_REQUIRED = {
    "fabric_inspection": ("individual_standard", "average_standard"),
    "correlation": ("standard", "scs"),
}

# Once a PLM exists, these define the physical identity of the consignment and must not
# diverge from the PLM snapshot. QC values may still be revised for FUTURE PLMs.
_LOCKED_AFTER_PLM = {
    "consignment", "supplier_id", "buyer_id", "received_quantity", "fabric_length",
    "quantity_unit", "fabric_type", "fabric_composition", "measurement_unit", "received_at",
}

_QC_VERSION_FIELDS = _LOCKED_AFTER_PLM | {"qc_config"}


def _plain_value(value):
    return getattr(value, "value", value)


def _changed(row: models.FabricReceive, key: str, value) -> bool:
    return _plain_value(getattr(row, key, None)) != _plain_value(value)


def _validate_finalized_setup(data: dict, current: models.FabricReceive | None = None) -> None:
    """Every consignment is ready for PLMs once saved (no draft/finalized step), so the
    Dashboard must always supply the context the factory app never re-asks for.
    """
    def effective(key, default=None):
        if key in data:
            return data[key]
        return getattr(current, key, default) if current is not None else default

    missing_context = []
    for key, label in (
        ("buyer_id", "buyer"),
        ("supplier_id", "supplier"),
        ("received_quantity", "received quantity"),
        ("measurement_unit", "measurement unit"),
    ):
        value = effective(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing_context.append(label)
    qty = effective("received_quantity")
    if qty is not None and float(qty) <= 0:
        raise HTTPException(status_code=422, detail="received_quantity must be greater than zero")

    qc = effective("qc_config", {}) or {}
    missing_qc = []
    invalid_qc = []
    for module, fields in _FINAL_QC_REQUIRED.items():
        section = qc.get(module) if isinstance(qc, dict) else None
        section = section if isinstance(section, dict) else {}
        for field in fields:
            value = section.get(field)
            if value is None or value == "":
                missing_qc.append(f"{module}.{field}")
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                invalid_qc.append(f"{module}.{field}")
                continue
            if number < 0:
                invalid_qc.append(f"{module}.{field}")

    if missing_context or missing_qc or invalid_qc:
        parts = []
        if missing_context:
            parts.append("missing context: " + ", ".join(missing_context))
        if missing_qc:
            parts.append("missing QC values: " + ", ".join(missing_qc))
        if invalid_qc:
            parts.append("invalid QC values: " + ", ".join(invalid_qc))
        raise HTTPException(status_code=422, detail="Final QC setup is incomplete — " + "; ".join(parts))


def _normalize_payload(
    db: Session,
    payload,
    *,
    exclude_unset: bool = False,
    current: models.FabricReceive | None = None,
):
    data = payload.model_dump(exclude_unset=exclude_unset)
    # New received_quantity supersedes the old fabric_length while preserving old clients.
    if data.get("received_quantity") is None and data.get("fabric_length") is not None:
        data["received_quantity"] = data["fabric_length"]
    if data.get("fabric_length") is None and data.get("received_quantity") is not None:
        data["fabric_length"] = data["received_quantity"]

    supplier_changed = "supplier_id" in data and (current is None or data.get("supplier_id") != current.supplier_id)
    supplier_id = data.get("supplier_id") if "supplier_id" in data else (current.supplier_id if current else None)
    if supplier_id:
        supplier = db.query(Supplier).get(supplier_id)
        if not supplier or not supplier.is_active:
            raise HTTPException(status_code=422, detail="Select an active supplier")
        # Unit is supplier-owned. Capture it on receive creation / supplier change, then keep
        # that consignment snapshot stable even if the supplier default changes later.
        if current is None or supplier_changed:
            data["measurement_unit"] = supplier.default_measurement_unit
        else:
            data.pop("measurement_unit", None)  # never manually override an existing receive

        defaults = get_supplier_qc_config(db, supplier_id)
        if current is None or supplier_changed:
            incoming = data.get("qc_config") or {}
            merged = deep_merge(defaults, incoming)
            # Shade points are supplier-owned definitions. Never allow stale points from a
            # previously selected supplier to leak into a new consignment. Once this receive
            # is created, the resulting list becomes the consignment snapshot.
            default_shade = ((defaults.get("shade_check") or {}).get("points") or []) if isinstance(defaults, dict) else []
            merged.setdefault("shade_check", {})["points"] = default_shade
            data["qc_config"] = merged
        elif "qc_config" in data and data["qc_config"] is not None:
            # Existing consignment edits are overrides of its own frozen setup, not a re-read
            # of today's supplier defaults. This keeps history deterministic.
            existing_config = current.qc_config or {}
            data["qc_config"] = deep_merge(existing_config, data["qc_config"])
    elif current is None:
        raise HTTPException(status_code=422, detail="Select an active supplier; measurement unit is supplier-owned")

    return data


@router.post("", response_model=schemas.FabricReceiveOut)
def create_fabric_receive(
    payload: schemas.FabricReceiveCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_admin()),
):
    duplicate = db.query(models.FabricReceive).filter(models.FabricReceive.consignment == payload.consignment).first()
    if duplicate:
        raise HTTPException(status_code=400, detail="A fabric receive with this consignment number already exists")
    data = _normalize_payload(db, payload)
    _validate_finalized_setup(data)
    row = models.FabricReceive(**data)
    row.qc_setup_status = "finalized"  # no draft step any more
    stamp_creator(row, user)
    db.add(row)
    db.flush()
    record_audit(db, user, action="fabric_receive_created", entity_type="fabric_receive", entity_id=row.id,
                 summary=f"Consignment {row.consignment} received")
    db.commit()
    db.refresh(row)
    return row


@router.put("/{receive_id}", response_model=schemas.FabricReceiveOut)
def update_fabric_receive(
    receive_id: UUID,
    payload: schemas.FabricReceiveUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_admin()),
):
    row = db.query(models.FabricReceive).get(receive_id)
    if not row:
        raise HTTPException(status_code=404, detail="Fabric receive record not found")
    data = _normalize_payload(db, payload, exclude_unset=True, current=row)
    if not data:
        return row

    # Physical consignment identity is immutable after downstream PLMs exist. This avoids
    # Buyer/Supplier/Unit/Fabric mismatches between an existing PLM and its parent receive.
    if row.plms:
        changed_locked = [key for key in _LOCKED_AFTER_PLM if key in data and _changed(row, key, data[key])]
        if changed_locked:
            raise HTTPException(
                status_code=422,
                detail="Cannot change consignment identity/planning fields after a PLM exists: " + ", ".join(sorted(changed_locked)),
            )

    if "consignment" in data and data["consignment"] != row.consignment:
        duplicate = db.query(models.FabricReceive).filter(
            models.FabricReceive.consignment == data["consignment"],
            models.FabricReceive.id != receive_id,
        ).first()
        if duplicate:
            raise HTTPException(status_code=400, detail="A fabric receive with this consignment number already exists")
    _validate_finalized_setup(data, current=row)
    # Version only actual planning/QC changes. Notes or no-op saves do not create fake
    # versions. Existing PLMs/reports keep their own snapshots.
    setup_changed = any(key in _QC_VERSION_FIELDS and _changed(row, key, value) for key, value in data.items())
    if setup_changed:
        row.qc_setup_version = (row.qc_setup_version or 1) + 1
    changed_keys = sorted(key for key, value in data.items() if _changed(row, key, value))
    for key, value in data.items():
        setattr(row, key, value)
    if changed_keys:
        record_audit(db, user, action="fabric_receive_updated", entity_type="fabric_receive", entity_id=row.id,
                     summary=f"Consignment {row.consignment} updated (v{row.qc_setup_version or 1}): "
                             + ", ".join(changed_keys))
    db.commit()
    db.refresh(row)
    return row


@router.get("", response_model=List[schemas.FabricReceiveOut])
def list_fabric_receives(
    response: Response,
    consignment: Optional[str] = None,
    buyer_id: Optional[UUID] = None,
    supplier_id: Optional[UUID] = None,
    q: Optional[str] = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    query = db.query(models.FabricReceive)
    if consignment:
        query = query.filter(models.FabricReceive.consignment.ilike(f"%{consignment}%"))
    if buyer_id:
        query = query.filter(models.FabricReceive.buyer_id == buyer_id)
    if supplier_id:
        query = query.filter(models.FabricReceive.supplier_id == supplier_id)
    query = apply_text_search(query, models.FabricReceive, q, ("consignment", "fabric_type", "fabric_composition", "notes"))
    query = apply_year_month(query, models.FabricReceive.received_at, year, month)
    query = query.order_by(models.FabricReceive.created_at.desc())
    return paginate(query, response, limit, offset).all()


@router.get("/{receive_id}", response_model=schemas.FabricReceiveOut)
def get_fabric_receive(receive_id: UUID, db: Session = Depends(get_db)):
    row = db.query(models.FabricReceive).get(receive_id)
    if not row:
        raise HTTPException(status_code=404, detail="Fabric receive record not found")
    return row


@router.delete("/{receive_id}", status_code=204)
def delete_fabric_receive(receive_id: UUID, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin())):
    """Deletes a consignment (fabric receive). Its PLMs must be deleted first, so a PLM and its
    QC reports never lose their consignment."""
    row = db.query(models.FabricReceive).get(receive_id)
    if not row:
        raise HTTPException(status_code=404, detail="Fabric receive record not found")
    if row.plms:
        codes = ", ".join(sorted(p.plm_code for p in row.plms)[:5])
        raise HTTPException(status_code=409, detail=f"Consignment {row.consignment} still has PLM(s): {codes}. Delete those PLMs first.")
    record_audit(db, user, action="fabric_receive_deleted", entity_type="fabric_receive", entity_id=row.id,
                 summary=f"Consignment {row.consignment} deleted")
    db.delete(row)
    db.commit()
    return Response(status_code=204)
