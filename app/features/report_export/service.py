from __future__ import annotations

from io import BytesIO
from html import escape
from typing import Iterable

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from sqlalchemy.orm import Session, joinedload

from app.features.plm.models import Plm, PlmColor, PlmRoll
from app.features.fabric_inspection import models as fi_models
from app.features.fabric_inspection.service import compute_report_stats as compute_fi_stats
from app.features.gsm import models as gsm_models, service as gsm_service
from app.features.moisture import models as moisture_models
from app.features.skew_bowing import models as skew_models, service as skew_service
from app.features.relaxation import models as relax_models, service as relax_service
from app.features.shrinkage import models as shrink_models, service as shrink_service
from app.features.shade_check import models as shade_models
from app.features.color_continuity import models as cc_models
from app.features.correlation import models as corr_models, routes as corr_routes, service as corr_service
from app.features.factory_report import routes as factory_routes


styles = getSampleStyleSheet()
TITLE = ParagraphStyle('PaperTitle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=15, leading=18, alignment=TA_CENTER, spaceAfter=4)
SUBTITLE = ParagraphStyle('PaperSubtitle', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, leading=12, alignment=TA_CENTER)
LABEL = ParagraphStyle('PaperLabel', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=7.5, leading=9)
CELL = ParagraphStyle('PaperCell', parent=styles['Normal'], fontName='Helvetica', fontSize=7.2, leading=8.6)
SMALL = ParagraphStyle('PaperSmall', parent=styles['Normal'], fontName='Helvetica', fontSize=6.5, leading=7.5)
CENTER = ParagraphStyle('PaperCenter', parent=CELL, alignment=TA_CENTER)


def _p(value, style=CELL):
    if value is None:
        value = ''
    if isinstance(value, float):
        value = f'{value:.2f}'
    return Paragraph(escape(str(value)), style)


def _fmt(v, digits=2):
    if v is None:
        return ''
    try:
        x = float(v)
        if abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
        return f'{x:.{digits}f}'
    except Exception:
        return str(v)


def _date(v):
    if not v:
        return ''
    try:
        return v.strftime('%d-%m-%Y')
    except Exception:
        return str(v)


def _plm(db: Session, plm_id):
    return db.query(Plm).options(joinedload(Plm.buyer), joinedload(Plm.supplier), joinedload(Plm.colors)).get(plm_id)


def _doc(buffer, *, landscape_page=False):
    page = landscape(A4) if landscape_page else A4
    return SimpleDocTemplate(
        buffer,
        pagesize=page,
        leftMargin=8*mm,
        rightMargin=8*mm,
        topMargin=8*mm,
        bottomMargin=8*mm,
        title='Interfab QC Report',
        author='Interfab Shirt Manufacturing Ltd.',
    )


def _table(data, widths=None, header_rows=1, font=7.0):
    wrapped=[]
    for ri,row in enumerate(data):
        style = ParagraphStyle(f'c{font}_{ri}', parent=CENTER, fontSize=font, leading=font+1)
        wrapped.append([_p(x, style) for x in row])
    t=Table(wrapped, colWidths=widths, repeatRows=header_rows, hAlign='LEFT')
    t.setStyle(TableStyle([
        ('GRID',(0,0),(-1,-1),0.45,colors.black),
        ('BACKGROUND',(0,0),(-1,header_rows-1),colors.HexColor('#E8E8E8')),
        ('FONTNAME',(0,0),(-1,header_rows-1),'Helvetica-Bold'),
        ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('ALIGN',(0,0),(-1,-1),'CENTER'),
        ('LEFTPADDING',(0,0),(-1,-1),2.3),('RIGHTPADDING',(0,0),(-1,-1),2.3),
        ('TOPPADDING',(0,0),(-1,-1),2.5),('BOTTOMPADDING',(0,0),(-1,-1),2.5),
    ]))
    return t


def _header_story(title: str, plm: Plm, *, color_text=''):
    buyer = plm.buyer.name if plm and plm.buyer else ''
    supplier = plm.supplier.name if plm and plm.supplier else ''
    style_plm = ' / '.join(x for x in [plm.plm_code if plm else '', plm.plm_style if plm else ''] if x)
    rows=[
        _p('Interfab Shirt Manufacturing Ltd.', TITLE),
        _p(title, SUBTITLE),
    ]
    meta = _table([
        ['Buyer', buyer, 'Supplier', supplier],
        ['PLM / Style', style_plm, 'Color', color_text],
        ['Consignment', getattr(plm,'consignment','') or '', 'Unit', (getattr(plm,'measurement_unit','') or '').value.upper() if hasattr(getattr(plm,'measurement_unit',''), 'value') else str(getattr(plm,'measurement_unit','') or '').upper()],
    ], widths=[24*mm,72*mm,24*mm,72*mm], header_rows=0, font=7.5)
    return [*rows, meta, Spacer(1,4*mm)]


def _fabric_inspection(db, plm):
    report=(db.query(fi_models.FabricInspectionReport)
            .options(joinedload(fi_models.FabricInspectionReport.rolls).joinedload(fi_models.FabricInspectionRoll.defects))
            .filter(fi_models.FabricInspectionReport.plm_id==plm.id).first())
    if not report: return None
    stats=compute_fi_stats(report)
    all_defect_names=[]
    for r in report.rolls:
        for d in r.defects:
            n=d.defect_type_name or ''
            if n and n not in all_defect_names: all_defect_names.append(n)
    defect_names=all_defect_names[:8]
    extra_defect_names=all_defect_names[8:]
    rows=[['Roll No','Color','Req W','Act W','Marked L','Actual L',*defect_names,'Total','% Points','Pts/Yds','Remarks']]
    for r in report.rolls:
        by={n:0 for n in defect_names}
        for d in r.defects:
            n=d.defect_type_name or ''
            if n in by: by[n]+=int(d.points_snapshot or 0)
        pts_yds=(float(r.total_points or 0)/float(r.length_actual)) if r.length_actual not in (None,0) else None
        rows.append([r.roll_no,r.colour_code,_fmt(r.width_marked),_fmt(r.width_actual),_fmt(r.length_marked),_fmt(r.length_actual),*[str(by[n]) if by[n] else '' for n in defect_names],r.total_points,_fmt(r.percent_points),_fmt(pts_yds),r.remarks or ''])
    story=_header_story('FABRICS INSPECTION REPORT - 4 POINTS SYSTEM',plm,color_text=report.color or '')
    story += [_table([
        ['Received Qty', f'{_fmt(report.received_qty)} {report.quantity_unit_snapshot.upper()}', 'Inspection Check Qty', f'{_fmt(report.checked_qty)} {report.quantity_unit_snapshot.upper()}'],
        ['Fabric Type', report.fabric_type or '', 'Fabric Composition', report.fabric_composition or ''],
        ['Individual Accepted', f'{_fmt(report.individual_standard_snapshot)}%', 'Average Accepted', f'{_fmt(report.average_standard_snapshot)}%'],
        ['In-house Date', _date(report.fabric_in_house_date), 'Inspection Date', _date(report.inspection_date)],
    ], widths=[30*mm,65*mm,34*mm,66*mm], header_rows=0, font=7.5), Spacer(1,3*mm)]
    pagew=landscape(A4)[0]-16*mm
    col_count=len(rows[0])
    fixed=[16,16,14,14,16,16]
    tail_width=64*mm
    remaining=pagew-sum(x*mm for x in fixed)-tail_width
    dyn=(remaining/max(1,len(defect_names))) if defect_names else 0
    widths=[16*mm,16*mm,14*mm,14*mm,16*mm,16*mm]+[dyn]*len(defect_names)+[13*mm,15*mm,14*mm,22*mm]
    story += [_table(rows,widths=widths,header_rows=1,font=5.7),Spacer(1,3*mm)]
    if extra_defect_names:
        extra=[['Roll No',*extra_defect_names]]
        for r in report.rolls:
            by={n:0 for n in extra_defect_names}
            for d in r.defects:
                n=d.defect_type_name or ''
                if n in by: by[n]+=int(d.points_snapshot or 0)
            extra.append([r.roll_no,*[str(by[n]) if by[n] else '' for n in extra_defect_names]])
        extra_width=(pagew-18*mm)/max(1,len(extra_defect_names))
        story += [_p('Additional Defect Breakdown',LABEL),_table(extra,widths=[18*mm]+[extra_width]*len(extra_defect_names),header_rows=1,font=5.4),Spacer(1,3*mm)]
    story += [_table([['Total Defect Points',stats['total_defect_points'],'Overall % Points',_fmt(stats['overall_percent_points']),'Result','PASS' if stats['is_pass'] else 'FAIL' if stats['is_pass'] is False else '']], widths=[32*mm,25*mm,32*mm,25*mm,22*mm,24*mm], header_rows=0, font=7.5)]
    return story, True


def _gsm(db, plm):
    r=db.query(gsm_models.GsmCheckReport).options(joinedload(gsm_models.GsmCheckReport.rolls)).filter(gsm_models.GsmCheckReport.plm_id==plm.id).first()
    if not r:return None
    stats=gsm_service.compute_report_stats(r.rolls,float(r.standard_gsm or 0),float(r.tolerance_snapshot) if r.tolerance_snapshot is not None else None)
    story=_header_story('GSM Check Report',plm,color_text=r.color or '')
    story += [_table([['Required GSM',_fmt(r.standard_gsm),'Tolerance',f'±{_fmt(r.tolerance_snapshot)}%','Date',_date(r.report_date)]], widths=[30*mm,24*mm,24*mm,24*mm,20*mm,32*mm],header_rows=0,font=8),Spacer(1,3*mm)]
    def section(title, after=False):
        data=[[title,'Roll No','Require GSM','Actual GSM','Over / Less %','Remarks']]
        count=0
        for x in r.rolls:
            actual=x.actual_gsm_after if after else x.actual_gsm_before
            if actual is None: continue
            count += 1
            pct=gsm_service.over_less_pct(float(actual),float(r.standard_gsm or 0))
            rem=x.remarks_after if after else x.remarks_before
            data.append(['',x.roll_no,_fmt(r.standard_gsm),_fmt(actual),_fmt(pct),rem or ''])
        if count:
            avg=stats['avg_over_less_after_pct' if after else 'avg_over_less_before_pct']
            passed=stats['pass_after' if after else 'pass_before']
            data.append(['', 'AVG','','',f'{_fmt(avg)}%','PASS' if passed is True else 'FAIL' if passed is False else ''])
        else:
            data.append(['','',_fmt(r.standard_gsm),'','',''])
        return _table(data,widths=[24*mm,22*mm,27*mm,27*mm,30*mm,44*mm],header_rows=1,font=7.3)
    story += [section('BEFORE WASH',False),Spacer(1,4*mm),section('AFTER WASH',True)]
    return story, False


def _moisture(db, plm):
    r=db.query(moisture_models.MoistureCheckReport).options(joinedload(moisture_models.MoistureCheckReport.rolls)).filter(moisture_models.MoistureCheckReport.plm_id==plm.id).first()
    if not r:return None
    story=_header_story('Moisture Check List',plm)
    moisture_cfg=(plm.qc_config or {}).get('moisture',{}) if isinstance(plm.qc_config,dict) else {}
    standard_label=moisture_cfg.get('standard_label') or plm.fabric_composition or 'Selected composition'
    story += [_table([
        ['Composition Standard',standard_label,'Acceptance',f'{_fmt(r.acceptance_snapshot)}%'],
        ['Vendor',r.vendor or '','Factory',r.factory or 'Interfab Shirt Mfg. Ltd.'],
    ], widths=[31*mm,65*mm,28*mm,71*mm],header_rows=0,font=7.5),Spacer(1,3*mm)]
    data=[['Roll No','Fabric Composition','Color','Rating','Pass/Fail','Wash','Date']]
    for x in r.rolls:
        for wash,val,d in [('BEFORE WASH',x.swatch_rating_before,x.before_wash_date),('AFTER WASH',x.swatch_rating_after,x.after_wash_date)]:
            if val is None:continue
            passed=float(val)<=float(r.acceptance_snapshot) if r.acceptance_snapshot is not None else None
            data.append([x.roll_no,x.fabric_composition or plm.fabric_composition or '',x.color or '',f'{_fmt(val)}%','PASS' if passed else 'FAIL' if passed is False else '',wash,_date(d)])
    story += [_table(data,widths=[19*mm,42*mm,22*mm,20*mm,22*mm,27*mm,27*mm],header_rows=1,font=7.1)]
    return story, False


def _skew(db, plm):
    r=db.query(skew_models.SkewBowingReport).options(joinedload(skew_models.SkewBowingReport.rolls)).filter(skew_models.SkewBowingReport.plm_id==plm.id).first()
    if not r:return None
    std=float(r.standard_point_snapshot) if r.standard_point_snapshot is not None else None
    stats=skew_service.compute_report_stats(r.rolls,std,r.measurement_unit_snapshot)
    story=_header_story('Skew & Bowing Check Report',plm)
    story += [_table([
        ['Receive Qty',f'{_fmt(r.receive_qty)} {r.quantity_unit_snapshot.upper()}','Total Roll',r.total_roll or '','Fabric Construction',r.fabric_construction or ''],
        ['In-house Date',_date(r.in_house_date),'Fabric Width Standard',r.fabric_width_label_snapshot or '','Fabric Type','STRIPE' if r.is_stripe else 'SOLID'],
        ['Acceptance',f'{_fmt(std)} {r.measurement_unit_snapshot.upper()}','','','',''],
    ],widths=[28*mm,36*mm,25*mm,25*mm,35*mm,45*mm],header_rows=0,font=7.5),Spacer(1,3*mm)]
    data=[['Wash','Roll No','Color','AC','BD','|AC-BD|','AC+BD','Skew %','Result']]
    for x in r.rolls:
        for wash,ac,bd in [('BEFORE',x.ac_value_before,x.bd_value_before),('AFTER',x.ac_value_after,x.bd_value_after)]:
            if ac is None and bd is None:continue
            pct=skew_service.skew_percent(ac,bd,None,r.measurement_unit_snapshot)
            amount=abs(float(ac)-float(bd)) if ac is not None and bd is not None else None
            total=float(ac)+float(bd) if ac is not None and bd is not None else None
            passed=amount<=std if amount is not None and std is not None else None
            data.append([wash,x.roll_no,x.colour_code or '',_fmt(ac),_fmt(bd),_fmt(amount),_fmt(total),f'{_fmt(pct)}%','PASS' if passed else 'FAIL' if passed is False else ''])
    avg_parts=[]
    if stats['avg_percent_before'] is not None: avg_parts.append(f"{_fmt(stats['avg_percent_before'])}% before")
    if stats['avg_percent_after'] is not None: avg_parts.append(f"{_fmt(stats['avg_percent_after'])}% after")
    data.append(['AVG','','','','','','',' / '.join(avg_parts),''])
    story += [_table(data,widths=[18*mm,19*mm,20*mm,19*mm,19*mm,20*mm,21*mm,27*mm,23*mm],header_rows=1,font=6.8)]
    return story, False


def _relaxation(db, plm):
    r=db.query(relax_models.FabricRelaxationReport).options(joinedload(relax_models.FabricRelaxationReport.rolls)).filter(relax_models.FabricRelaxationReport.plm_id==plm.id).first()
    if not r:return None
    story=_header_story('FABRIC RELAXATION TEST REPORT',plm,color_text=r.color or '')
    data=[['Color','Roll No',f'Fabric {plm.quantity_unit.upper()}','Start Time','Before L','Before W','After Time','After L','After W','Change L %','Change W %','Remark']]
    for x in r.rolls:
        f=relax_service.compute_roll_fields(x)
        data.append([x.color_code or '',x.roll_no,_fmt(x.fabric_yds_mtr),x.before_relax_start_time or '',_fmt(x.before_relax_length),_fmt(x.before_relax_width),x.after_relax_time or '',_fmt(x.after_relax_length),_fmt(x.after_relax_width),_fmt(f['shrinkage_length_pct']),_fmt(f['shrinkage_width_pct']),x.remark or ''])
    story += [_table(data,widths=[18*mm,18*mm,20*mm,22*mm,18*mm,18*mm,22*mm,18*mm,18*mm,22*mm,22*mm,32*mm],header_rows=1,font=6.3),Spacer(1,3*mm)]
    story += [_p(f"Relaxation time: {r.relaxation_hours_snapshot or 12} HOURS", LABEL)]
    return story, True


def _shrinkage(db, plm):
    r=db.query(shrink_models.ShrinkagePatternReport).options(joinedload(shrink_models.ShrinkagePatternReport.rolls).joinedload(shrink_models.ShrinkageRoll.measurements)).filter(shrink_models.ShrinkagePatternReport.plm_id==plm.id).first()
    if not r:return None
    story=_header_story('Shrinkage & Pattern Adjustment Report',plm,color_text=r.color or '')
    data=[['Roll','Qty','Reading','Before L','Before W','After L','After W','L %','W %','Pattern','Shade']]
    for x in r.rolls:
        for m in sorted(x.measurements,key=lambda z:z.reading_order):
            f=shrink_service.compute_measurement_fields(m)
            data.append([x.roll_no,_fmt(x.yds),m.reading_order,_fmt(m.before_length),_fmt(m.before_width),_fmt(m.after_length),_fmt(m.after_width),_fmt(f['adjust_length_pct']),_fmt(f['adjust_width_pct']),x.pattern or '',x.shade or ''])
    story += [_table(data,widths=[20*mm,18*mm,16*mm,20*mm,20*mm,20*mm,20*mm,20*mm,20*mm,18*mm,18*mm],header_rows=1,font=6.8)]
    return story, True


def _shade(db, plm):
    r=db.query(shade_models.ShadeCheckReport).options(joinedload(shade_models.ShadeCheckReport.rolls).joinedload(shade_models.ShadeCheckRoll.results)).filter(shade_models.ShadeCheckReport.plm_id==plm.id).first()
    if not r:return None
    points=sorted((r.points_snapshot or []),key=lambda x:int(x.get('sort_order',0)))
    length=[p for p in points if p.get('check_type')=='length']
    width=[p for p in points if p.get('check_type')=='width']
    story=_header_story('Fabric Shade Check Report',plm)
    headers=['Date','Colour','Cons','Roll No']+[p.get('label',p.get('check_key','')) for p in length]
    for p in width:
        headers += [f"{p.get('label','')} FIRST",f"{p.get('label','')} MIDDLE",f"{p.get('label','')} LAST"]
    headers += ['Remarks']
    data=[headers]
    for roll in r.rolls:
        by={x.point_key:x for x in roll.results}
        row=[_date(roll.roll_date),roll.colour or '',_fmt(roll.cons),roll.roll_no]
        for p in length:
            x=by.get(p.get('check_key')); row.append('OK' if x and x.first_ok is True else 'NG' if x and x.first_ok is False else '')
        for p in width:
            x=by.get(p.get('check_key'))
            for v in [getattr(x,'first_ok',None) if x else None,getattr(x,'middle_ok',None) if x else None,getattr(x,'last_ok',None) if x else None]: row.append('OK' if v is True else 'NG' if v is False else '')
        row.append(roll.remarks or '')
        data.append(row)
    available=landscape(A4)[0]-16*mm
    widths=[18*mm,20*mm,16*mm,18*mm]
    remaining_cols=len(headers)-5
    rem=available-sum(widths)-25*mm
    each=rem/max(1,remaining_cols)
    widths += [each]*remaining_cols+[25*mm]
    story += [_table(data,widths=widths,header_rows=1,font=5.4)]
    return story, True


def _continuity(db, plm):
    r=db.query(cc_models.ColorContinuityCard).options(joinedload(cc_models.ColorContinuityCard.rolls)).filter(cc_models.ColorContinuityCard.plm_id==plm.id).first()
    if not r:return None
    story=_header_story('Master Color Continuity Card',plm)
    data=[['Roll No','Before Wash / Non Wash Shade','After Wash Shade',f'Qty ({plm.quantity_unit.upper()})']]
    for x in r.rolls:data.append([x.roll_no,x.shade_group_before or '',x.shade_group_after or '',_fmt(x.yardage)])
    story += [_table(data,widths=[32*mm,62*mm,62*mm,35*mm],header_rows=1,font=8)]
    return story, False


def _correlation(db, plm):
    report=corr_routes._ensure_report(db,plm)
    corr_routes._sync_from_sources(db,report,plm)
    db.flush()
    report=(db.query(corr_models.FabricCorrelationReport).options(joinedload(corr_models.FabricCorrelationReport.rolls).joinedload(corr_models.FabricCorrelationRoll.defects)).get(report.id))
    for row in report.rolls:
        for k,v in corr_service.compute_roll_fields(row,report.measurement_unit_snapshot,report.quantity_unit_snapshot).items():setattr(row,k,v)

    inner_widths=[12*mm,25*mm,14*mm,14*mm,15*mm,17*mm,13*mm,8*mm,14*mm]
    story=[]
    rolls=list(report.rolls or [])
    # Nested side-by-side tables cannot split safely inside one parent cell, so paginate
    # in small chunks. This keeps 40/50+ roll client reports inside A4 with no clipping.
    chunks=[rolls[i:i+9] for i in range(0,len(rolls),9)] or [[]]
    for ci,chunk in enumerate(chunks):
        if ci:
            story.append(PageBreak())
        story += _header_story('Interfab Shirt Manufacturing Ltd. Fabric Correlation Report',plm)
        mill=[['Roll No','Defect Point',f'Length ({report.quantity_unit_snapshot.upper()})',f'Width ({report.measurement_unit_snapshot.upper()})','Total Point','Average Point','Standard','SCS','Pass/Fail']]
        factory=[['Roll No','Defects',f'Length ({report.quantity_unit_snapshot.upper()})',f'Width ({report.measurement_unit_snapshot.upper()})','Total Point','Average Point','Standard','SCS','Pass/Fail']]
        for x in chunk:
            active=[]
            for d in x.defects:
                if int(d.count or 0) <= 0:
                    continue
                name=d.defect_type_name_snapshot or 'Defect'
                label=d.range_label_snapshot or ''
                active.append(f'{name} {label}: {int(d.count)} / {int(d.points_snapshot or 0)}pt')
            mill.append([
                x.roll_no,'Supplier Total',_fmt(x.length_yards),_fmt(x.width),_fmt(x.mill_total_point),
                _fmt(getattr(x,'mill_average_point',None)),_fmt(x.standard),x.scs,
                'PASS' if getattr(x,'mill_pass',None) is True else 'FAIL' if getattr(x,'mill_pass',None) is False else 'PENDING',
            ])
            factory.append([
                x.roll_no,'; '.join(active) if active else 'No defect',_fmt(x.factory_length),_fmt(x.factory_width),_fmt(x.factory_total_points),
                _fmt(getattr(x,'factory_average_point',None)),_fmt(x.standard),x.scs,
                'PASS' if getattr(x,'factory_pass',None) is True else 'FAIL' if getattr(x,'factory_pass',None) is False else 'PENDING',
            ])
        left=[_p('Mill Report', SUBTITLE), Spacer(1,1.5*mm), _table(mill,widths=inner_widths,header_rows=1,font=4.4)]
        right=[_p('Factory Report', SUBTITLE), Spacer(1,1.5*mm), _table(factory,widths=inner_widths,header_rows=1,font=4.4)]
        holder=Table([[left,right]], colWidths=[136*mm,136*mm], hAlign='LEFT')
        holder.setStyle(TableStyle([
            ('VALIGN',(0,0),(-1,-1),'TOP'),
            ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),4),
            ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0),
        ]))
        story.append(holder)
    return story, True


def _range_text(values):
    vals=[float(v) for v in values if v is not None]
    if not vals:
        return ''
    lo=min(vals); hi=max(vals)
    return _fmt(lo) if abs(lo-hi)<1e-9 else f'{_fmt(lo)} - {_fmt(hi)}'


def _four_point_for_export(db, plm_id):
    return (db.query(fi_models.FabricInspectionReport)
            .options(joinedload(fi_models.FabricInspectionReport.rolls))
            .filter(fi_models.FabricInspectionReport.plm_id==plm_id).first())


def _factory(db, plm):
    report=factory_routes._ensure_derived(db,plm)
    if not report:return None

    # The source paper prints supplier/mill shipment context beside the factory sample.
    # Supplier totals come from Dashboard roll planning; factory totals come only from the
    # saved 4-point inspection. No worker re-entry is needed for either side.
    planned=(db.query(PlmRoll,PlmColor)
             .join(PlmColor,PlmRoll.plm_color_id==PlmColor.id)
             .filter(PlmColor.plm_id==plm.id)
             .order_by(PlmColor.colour_code,PlmRoll.roll_no).all())
    mill_points=sum(float(r.supplier_total_point or 0) for r,c in planned)
    mill_rolls=[r for r,c in planned if r.marked_length is not None and r.marked_width is not None]
    mill_area=0.0
    mill_length=0.0
    mill_widths=[]
    for r in mill_rolls:
        length=float(r.marked_length or 0); width=float(r.marked_width or 0)
        if length>0 and width>0:
            mill_length += length
            mill_widths.append(width)
            try:
                from app.features.fabric_inspection.service import width_to_inches, length_to_yards, FOUR_POINT_CONSTANT
                mill_area += width_to_inches(width, plm.measurement_unit.value if hasattr(plm.measurement_unit,'value') else str(plm.measurement_unit)) * length_to_yards(length, plm.quantity_unit)
            except ValueError:
                pass
    mill_avg=(mill_points*3600/mill_area) if mill_area>0 else None
    standard=float(report.standard_snapshot) if report.standard_snapshot is not None else None
    mill_pass=(mill_avg<=standard) if mill_avg is not None and standard is not None else None

    source=_four_point_for_export(db,plm.id)
    factory_widths=[float(r.width_actual) for r in (source.rolls if source else []) if r.width_actual is not None and float(r.width_actual)>0]
    mill_width_text=_range_text(mill_widths)
    factory_width_text=_range_text(factory_widths)

    story=_header_story('Factory Report',plm,color_text=report.color or '')

    def summary_panel(title, *, yardage, pieces, width_text, total_point, avg_point, percentage, stage, status):
        info=_table([
            ['Customer',plm.buyer.name if plm.buyer else '','Job No.',report.job_no or plm.plm_code],
            ['Construction',report.construction or plm.fabric_composition or '','Color',report.color or ''],
            ['Order Qty',f'{_fmt(report.order_qty)} {plm.quantity_unit.upper()}','Inspection Date',_date(report.inspection_date)],
        ],widths=[20*mm,45*mm,18*mm,45*mm],header_rows=0,font=6.1)
        status_table=_table([
            ['Inspection Yardage',f'{_fmt(yardage)} {plm.quantity_unit.upper()}','Inspection Pieces',pieces],
            ['Actual Width',width_text,'Inspection %',f'{_fmt(percentage)}%' if percentage is not None else ''],
            ['Inspection Stage',stage,'Total Point',_fmt(total_point)],
        ],widths=[27*mm,35*mm,27*mm,39*mm],header_rows=0,font=5.9)
        result=_table([
            ['Average Shipment Point','Actual','Standard','Pass/Fail'],
            ['',_fmt(avg_point),_fmt(standard),'PASS' if status is True else 'FAIL' if status is False else 'PENDING'],
        ],widths=[44*mm,27*mm,27*mm,30*mm],header_rows=1,font=6.2)
        return [_p(title,SUBTITLE),Spacer(1,1.5*mm),info,Spacer(1,2*mm),_p('Inspection Status',LABEL),status_table,Spacer(1,2*mm),_p('Result',LABEL),result]

    mill_pct=100.0 if mill_length>0 else None
    left=summary_panel('Supplier / Mill Summary',yardage=mill_length or plm.allocated_quantity,pieces=len(planned),width_text=mill_width_text,total_point=mill_points,avg_point=mill_avg,percentage=mill_pct,stage='Supplier Report',status=mill_pass)
    right=summary_panel('Factory Inspection',yardage=report.inspection_yardage,pieces=report.inspection_pieces or 0,width_text=factory_width_text,total_point=report.total_point,avg_point=report.avg_shipment_point,percentage=report.inspection_percentage,stage=report.inspection_stage or 'Final',status=report.is_pass)
    holder=Table([[left,right]],colWidths=[136*mm,136*mm],hAlign='LEFT')
    holder.setStyle(TableStyle([
        ('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),4),
        ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0),
    ]))
    story += [holder]
    return story, True


BUILDERS={
    'fabric-inspection': _fabric_inspection,
    'gsm': _gsm,
    'moisture': _moisture,
    'skew-bowing': _skew,
    'relaxation': _relaxation,
    'shrinkage': _shrinkage,
    'shade-check': _shade,
    'color-continuity': _continuity,
    'correlation': _correlation,
    'factory-report': _factory,
}


def build_report_pdf(db: Session, module: str, plm_id) -> bytes:
    plm=_plm(db,plm_id)
    if not plm:
        raise LookupError('PLM not found')
    builder=BUILDERS.get(module)
    if not builder:
        raise KeyError(f'Unsupported report module: {module}')
    result=builder(db,plm)
    if not result:
        raise ValueError('Report data is not ready yet')
    story,is_landscape=result
    buffer=BytesIO()
    doc=_doc(buffer,landscape_page=is_landscape)
    doc.build(story)
    return buffer.getvalue()
