from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.features.report_export.service import build_report_pdf, BUILDERS
from app.features.report_export.analytics_pdf import ANALYSIS_KINDS, build_analysis_pdf
from app.features.plm.models import Plm

router = APIRouter(prefix='/reports', tags=['Canonical Printable Reports'])


@router.get('/pdf/analysis/{kind}')
def analysis_pdf(
    kind: str,
    year: int | None = None,
    month: int | None = None,
    buyer_id: UUID | None = None,
    supplier_id: UUID | None = None,
    db: Session = Depends(get_db),
):
    if kind not in ANALYSIS_KINDS:
        raise HTTPException(status_code=404, detail='Unknown analysis report')
    try:
        pdf = build_analysis_pdf(db, kind, year=year, month=month, buyer_id=buyer_id, supplier_id=supplier_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    filename = f"interfab-{kind}-{year or 'all'}-{month or 'all'}.pdf"
    return Response(
        content=pdf,
        media_type='application/pdf',
        headers={'Content-Disposition': f'inline; filename="{filename}"'},
    )


@router.get('/pdf/{module}/{plm_id}')
def report_pdf(module: str, plm_id: UUID, db: Session = Depends(get_db)):
    if module not in BUILDERS:
        raise HTTPException(status_code=404, detail='Unknown report module')
    try:
        pdf = build_report_pdf(db, module, plm_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    plm = db.query(Plm).get(plm_id)
    safe_code = ''.join(ch if ch.isalnum() or ch in ('-','_') else '-' for ch in ((plm.plm_code if plm else None) or 'PLM'))
    return Response(
        content=pdf,
        media_type='application/pdf',
        headers={'Content-Disposition': f'inline; filename="{module}-{safe_code}.pdf"'},
    )
