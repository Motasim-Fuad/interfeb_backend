from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from app.core.sync import client_stamp, is_stale, mark_stale, retry_on_conflict
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.list_query import bound_unscoped
from app.core.deps import require_roles
from app.features.color_continuity import models, schemas
from app.features.inspection_report.service import upsert_inspection_report

router = APIRouter(prefix="/color-continuity", tags=["Color Continuity Card"])


def _attach_totals(card: models.ColorContinuityCard):
    card.total_roll = len(card.rolls)
    card.total_yardage = sum(float(r.yardage) for r in card.rolls if r.yardage is not None)
    return card


@router.post("", response_model=schemas.CardOut)
@retry_on_conflict
def create_card(payload: schemas.CardCreate, db: Session = Depends(get_db), _=Depends(require_roles("inspection"))):
    existing = db.query(models.ColorContinuityCard).options(joinedload(models.ColorContinuityCard.rolls)).filter(models.ColorContinuityCard.plm_id == payload.plm_id).first()
    if existing:
        return _attach_totals(existing)
    card = models.ColorContinuityCard(**payload.model_dump())
    db.add(card)
    db.commit()
    db.refresh(card)
    _attach_totals(card)
    return card


@router.get("", response_model=List[schemas.CardOut])
def list_cards(response: Response, plm_id: Optional[UUID] = None, limit: Optional[int] = None, offset: int = 0, db: Session = Depends(get_db)):
    q = db.query(models.ColorContinuityCard).options(joinedload(models.ColorContinuityCard.rolls))
    if plm_id:
        q = q.filter(models.ColorContinuityCard.plm_id == plm_id)
    cards = bound_unscoped(q.order_by(models.ColorContinuityCard.created_at.desc()), bool(plm_id), response, limit, offset).all()
    for card in cards:
        _attach_totals(card)
    return cards


@router.post("/{card_id}/rolls", response_model=schemas.RollOut)
@retry_on_conflict
def upsert_roll(card_id: UUID, payload: schemas.RollIn, response: Response, db: Session = Depends(get_db), _=Depends(require_roles("inspection", "worker"))):
    card = db.query(models.ColorContinuityCard).get(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    roll = (
        db.query(models.ColorContinuityRoll)
        .filter(models.ColorContinuityRoll.card_id == card_id, models.ColorContinuityRoll.roll_no == payload.roll_no)
        .first()
    )
    if is_stale(roll, payload.client_updated_at):
        mark_stale(response)
        return roll
    data = payload.model_dump()
    data["client_updated_at"] = client_stamp(payload.client_updated_at)
    if roll:
        for k, v in data.items():
            setattr(roll, k, v)
    else:
        roll = models.ColorContinuityRoll(card_id=card_id, **data)
        db.add(roll)

    db.commit()
    db.refresh(roll)

    upsert_inspection_report(
        db, plm_id=card.plm_id, roll_number=roll.roll_no,
        module_field="color_continuity_card_id", module_row_id=roll.id,
    )
    db.commit()
    return roll
