"""Printable reports laid out like the factory's paper forms (TIDEWATER sample pack).

Each builder returns (story, landscape). Every value printed comes from saved data; blank
cells stay blank instead of inventing numbers. Photos sit where the paper forms keep them
(4-point photos go below the score sheet, which has no photo on paper).
"""
from __future__ import annotations

import os
from collections import OrderedDict

from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Image, KeepTogether, PageBreak, Spacer, Table, TableStyle
from sqlalchemy.orm import joinedload

from app.core.moisture_limits import FIBER_LIMITS
from app.features.admin_config.models import DefectRangeConfig, DefectType
from app.features.report_export import service as S
from app.features.skew_bowing import service as skew_service
from app.features.fabric_inspection.service import width_to_inches

INK = S.INK
LINE = S.LINE
HEAD = S.HEADER_BG
LOGO_PATH = os.path.join(os.path.dirname(__file__), 'assets', 'isml_logo.png')

BIG = ParagraphStyle('PfBig', parent=S.TITLE, fontSize=17, leading=20)
MID = ParagraphStyle('PfMid', parent=S.SUBTITLE, fontSize=11, leading=13)
KV = ParagraphStyle('PfKv', parent=S.META_VALUE, fontSize=8, leading=10)
KVB = ParagraphStyle('PfKvb', parent=S.META_LABEL, fontSize=8, leading=10)
NOTE = ParagraphStyle('PfNote', parent=S.CELL, fontSize=7.5, leading=9.5, alignment=TA_LEFT)
CAP = ParagraphStyle('PfCap', parent=S.SMALL, fontName='Helvetica-Bold', fontSize=7.5, leading=9)


def _m(value, style=None):
    """Paragraph that keeps simple markup (<b>, <br/>); falls back to plain text if a value breaks it."""
    from html import escape
    from reportlab.platypus import Paragraph

    style = style or S.CELL
    if value is None:
        value = ''
    if isinstance(value, float):
        value = f'{value:.2f}'
    text = str(value)
    try:
        return Paragraph(text, style)
    except Exception:
        import re
        return Paragraph(escape(re.sub(r'<[^>]+>', ' ', text)), style)


def _w(landscape_page):
    # The page frame keeps 6pt padding on each side; tables must fit inside it.
    return S._page_content_width(landscape_page=landscape_page) - 12


def _logo(height=11*mm):
    if not os.path.isfile(LOGO_PATH):
        return _m('ISML', S.LABEL)
    img = Image(LOGO_PATH, width=height * 200 / 75, height=height, mask='auto')
    img.hAlign = 'CENTER'
    return img


def _box(rows, widths, *, grid=True, cmds=(), heights=None):
    t = Table(rows, colWidths=widths, rowHeights=heights)
    style = [
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]
    if grid:
        style.append(('GRID', (0, 0), (-1, -1), 0.5, LINE))
    style += list(cmds)
    t.setStyle(TableStyle(style))
    return t


def _title_bar(title, page_w, *, right=None, company='Interfab Shirt Mfg. Ltd.'):
    """[logo + company] | [FORM TITLE] | [DATE / doc box] — the boxed top line of every form."""
    left = Table([[_logo(9*mm), _m(company, ParagraphStyle('co', parent=S.LABEL, fontSize=10, leading=12))]],
                 colWidths=[26*mm, None])
    left.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (-1, -1), 2)]))
    right_cell = right if right is not None else ''
    if isinstance(right_cell, str):
        right_cell = _m(right_cell, ParagraphStyle('rt', parent=S.LABEL, fontSize=10, leading=12))
    lw, rw = 70*mm, 48*mm
    return _box([[left, _m(title, BIG), right_cell]], [lw, page_w - lw - rw, rw],
                cmds=[('ALIGN', (1, 0), (1, 0), 'CENTER')], heights=[15*mm])


def _signatures(labels, page_w, height=16*mm):
    cells = [[_m('', NOTE) for _ in labels], [_m(l, CAP) for l in labels]]
    return _box(cells, [page_w / len(labels)] * len(labels), heights=[height, 6*mm],
                cmds=[('LINEABOVE', (0, 1), (-1, 1), 0.6, LINE), ('ALIGN', (0, 0), (-1, -1), 'CENTER')])


def _kv_rows(pairs, page_w, cols=2, label_w=30*mm):
    """Label | value | label | value grid (paper header block)."""
    rows = []
    for i in range(0, len(pairs), cols):
        row = []
        for label, value in pairs[i:i + cols]:
            row += [_m(label, KVB), _m(value, KV)]
        while len(row) < cols * 2:
            row += ['', '']
        rows.append(row)
    value_w = (page_w - label_w * cols) / cols
    widths = [label_w, value_w] * cols
    cmds = [('BACKGROUND', (c, 0), (c, -1), HEAD) for c in range(0, cols * 2, 2)]
    return _box(rows, widths, cmds=cmds)


def _photo(db, owner_type, owner_id, size, *, fallback_types=(), fallback_roll_type=None, roll_ids=()):
    img = S._stage_photo(db, owner_type, owner_id, fallback_types=fallback_types,
                         fallback_roll_type=fallback_roll_type, roll_ids=roll_ids, max_w=size[0], max_h=size[1])
    return S._ClippedPhoto(img, size[0], size[1]) if img is not None else None


def _photo_box(photo, caption, size, page_w=None):
    inner = photo if photo is not None else _m('No image uploaded', S.SMALL)
    w = page_w or size[0] + 8
    return _box([[inner], [_m(caption, CAP)]], [w], heights=[size[1] + 8, 7*mm],
                cmds=[('ALIGN', (0, 0), (-1, -1), 'CENTER')])


def _buyer(plm):
    return plm.buyer.name if plm and plm.buyer else ''


def _supplier(plm):
    return plm.supplier.name if plm and plm.supplier else ''


def _colors(plm, extra=()):
    return ', '.join(S._plm_colors(plm, list(extra) + list(S._planned_color_map(plm).values())))


def _total_rolls(plm):
    return sum(len(c.rolls) for c in (plm.colors or []))


def _pf(v):
    return 'PASS' if v is True else 'FAIL' if v is False else ''


# --------------------------------------------------------------------------------------------
# 4 POINT — Fabrics Inspection Report (paper page 1) + photos below
# --------------------------------------------------------------------------------------------

def _range_table(db):
    """Length of Defect -> Points Considered, from the Dashboard defect ranges.

    Types sharing the same size -> points list print once (the common list, as on paper);
    a type with its own list (e.g. Hole) gets an "In Case of HOLE" block.
    """
    rows = db.query(DefectRangeConfig).filter(DefectRangeConfig.is_active == True).order_by(  # noqa: E712
        DefectRangeConfig.sort_order, DefectRangeConfig.points).all()
    active = {t.id: t.name for t in db.query(DefectType).filter(DefectType.is_active == True)}  # noqa: E712
    per_type: OrderedDict = OrderedDict()
    for r in rows:
        if r.defect_type_id is not None and r.defect_type_id not in active:
            continue
        per_type.setdefault(r.defect_type_id, []).append((r.range_label.strip(), int(r.points)))
    groups: OrderedDict = OrderedDict()  # (label, points) list -> type names
    for type_id, ranges in per_type.items():
        groups.setdefault(tuple(ranges), []).append(active.get(type_id) if type_id else None)
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), None not in kv[1]))
    data = [[_m('Length of Defect', CAP), _m('Points Considered', CAP)]]
    cmds = [('BACKGROUND', (0, 0), (-1, 0), HEAD)]
    for i, (ranges, names) in enumerate(ordered):
        if i > 0:
            label = ' / '.join(n.upper() for n in names if n) or 'OTHERS'
            data.append([_m(f'In Case of {label}', CAP), ''])
            cmds += [('SPAN', (0, len(data) - 1), (1, len(data) - 1)), ('BACKGROUND', (0, len(data) - 1), (1, len(data) - 1), HEAD)]
        for label, pts in ranges:
            data.append([_m(label, S.SMALL), _m(str(pts), S.SMALL)])
    t = Table(data, colWidths=[28*mm, 28*mm])
    t.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.4, LINE), ('TOPPADDING', (0, 0), (-1, -1), 1),
                           ('BOTTOMPADDING', (0, 0), (-1, -1), 1), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE')] + cmds))
    return t


def fabric_inspection(db, plm):
    from app.features.fabric_inspection import models as fi
    from app.features.fabric_inspection.service import compute_report_stats

    report = (db.query(fi.FabricInspectionReport)
              .options(joinedload(fi.FabricInspectionReport.rolls).joinedload(fi.FabricInspectionRoll.defects))
              .filter(fi.FabricInspectionReport.plm_id == plm.id).first())
    if not report:
        return None
    pw = _w(True)
    stats = compute_report_stats(report)
    qu = (report.quantity_unit_snapshot or 'yds').upper()
    mu = (report.measurement_unit_snapshot or 'inch').upper()
    rolls = sorted(report.rolls, key=lambda r: r.created_at or 0)
    checked = report.checked_qty if report.checked_qty is not None else (
        sum(float(r.length_actual) for r in rolls if r.length_actual is not None) or None)
    planned = S._planned_color_map(plm)

    # Top strip (above the boxed form on paper).
    top = _box([[
        _m(f'<b>INSPECTION CHECK QTY:</b> {S._fmt(checked)} {qu}<br/><b>FABRIC TYPE:</b> {report.fabric_type or ""}'
             f'<br/><b>FABRIC COMPOSITION:</b> {report.fabric_composition or ""}', NOTE),
        _m(f'<b>INDIVIDUAL ACCEPTED:</b> {S._fmt(report.individual_standard_snapshot)}%<br/>'
             f'<b>AVERAGE ACCEPTED:</b> {S._fmt(report.average_standard_snapshot)}%<br/>'
             f'<b>FABRIC IN HOUSE DATE:</b> {S._date(report.fabric_in_house_date or plm.received_at)}<br/>'
             f'<b>CONSIGNMENT:</b> {plm.consignment or ""} &nbsp; <b>TOTAL ROLL:</b> {_total_rolls(plm)}', NOTE),
    ]], [pw / 2, pw / 2], grid=False)
    story = [top, Spacer(1, 2*mm),
             _title_bar('FABRICS INSPECTION REPORT', pw, right=f'DATE : {S._date(report.inspection_date)}')]

    # Header block: party info | received / checked | method | range table.
    overall = stats['overall_percent_points']
    area_text = ''
    widths_in = [width_to_inches(float(r.width_actual), report.measurement_unit_snapshot) for r in rolls if r.width_actual]
    if overall is not None:
        avg_w = S._fmt(sum(widths_in) / len(widths_in)) if widths_in else ''
        area_text = (f'No of Points × 3600 ÷ (Width inch × Length yds) = {stats["total_defect_points"]} × 3600 ÷ '
                     f'({avg_w} in × {S._fmt(stats["total_length_actual"])} {qu.lower()}) = <b>{S._fmt(overall)} Points</b>')
    party = _m(f'<b>Buyer:</b> {_buyer(plm)}<br/><b>PLM-Style:</b> {plm.plm_code}'
                 f'{" / " + plm.plm_style if plm.plm_style and plm.plm_style != plm.plm_code else ""}'
                 f'<br/><b>Color:</b> {_colors(plm, [r.colour_code for r in rolls])}<br/><b>Supplier Name:</b> {_supplier(plm)}', NOTE)
    qty = _m(f'<b>Received Qty:</b> {S._fmt(report.received_qty)} {qu}<br/><br/><b>Checked Qty:</b> {S._fmt(checked)} {qu}', NOTE)
    method = _m('<b>METHOD OF INSPECTION</b><br/><font size="11"><b>4 POINTS SYSTEM</b></font><br/><br/>' + area_text,
                  ParagraphStyle('m', parent=NOTE, alignment=TA_CENTER))
    head = _box([[party, qty, method, _range_table(db)]], [70*mm, 45*mm, pw - 70*mm - 45*mm - 60*mm, 60*mm],
                cmds=[('VALIGN', (0, 0), (-1, -1), 'TOP')])
    story += [head]

    # Roll table — one column per Dashboard defect type (plus any type only seen in data).
    names = [t.name for t in db.query(DefectType).filter(DefectType.is_active == True).order_by(DefectType.sort_order, DefectType.name)]  # noqa: E712
    for r in rolls:
        for d in r.defects:
            if d.defect_type_name and d.defect_type_name not in names:
                names.append(d.defect_type_name)
    h1 = ['Roll No.', 'Colour Code', 'FABRICS WIDTH', '', 'FABRICS LENGTH', '', 'FABRICS DEFECT'] + [''] * (len(names) - 1) + \
         ['Total Points', '% Points', 'Points Per Yards', 'REMARKS']
    h2 = ['', '', f'Reqr ({mu})', f'Actual ({mu})', f'Marked ({qu})', f'Actual ({qu})'] + names + ['', '', '', '']
    data = [h1, h2]
    sums = {'ml': 0.0, 'al': 0.0, 'pts': 0, **{n: 0 for n in names}}
    for r in rolls:
        by = {n: 0 for n in names}
        for d in r.defects:
            by[d.defect_type_name] = by.get(d.defect_type_name, 0) + int(d.points_snapshot or 0)
        pts_yds = float(r.total_points or 0) / float(r.length_actual) if r.length_actual else None
        data.append([r.roll_no, S._color_for(plm, r.roll_no, r.colour_code, planned), S._fmt(r.width_marked), S._fmt(r.width_actual),
                     S._fmt(r.length_marked), S._fmt(r.length_actual), *[str(by[n]) if by[n] else '' for n in names],
                     r.total_points or 0, S._fmt(r.percent_points), S._fmt(pts_yds), r.remarks or ''])
        sums['ml'] += float(r.length_marked or 0)
        sums['al'] += float(r.length_actual or 0)
        sums['pts'] += int(r.total_points or 0)
        for n in names:
            sums[n] += by[n]
    data.append(['TOTAL', '', '', '', S._fmt(sums['ml']), S._fmt(sums['al']), *[str(sums[n]) if sums[n] else '' for n in names],
                 sums['pts'], S._fmt(overall), '', ''])
    fixed = [15, 24, 15, 15, 16, 16]
    tail = [15, 15, 15, 24]
    dyn_w = (pw - sum(fixed + tail) * mm) / max(1, len(names))
    widths = [x*mm for x in fixed] + [dyn_w] * len(names) + [x*mm for x in tail]
    d0 = 6
    spans = [('SPAN', (0, 0), (0, 1)), ('SPAN', (1, 0), (1, 1)), ('SPAN', (2, 0), (3, 0)), ('SPAN', (4, 0), (5, 0)),
             ('SPAN', (d0, 0), (d0 + len(names) - 1, 0))]
    for i in range(4):
        c = d0 + len(names) + i
        spans.append(('SPAN', (c, 0), (c, 1)))
    font = 6.4 if len(names) <= 9 else 5.6
    story += [S._table(data, widths=widths, header_rows=2, font=font, spans=spans,
                       extra_cmds=[('FONTNAME', (0, len(data) - 1), (-1, len(data) - 1), 'Helvetica-Bold')])]

    # Comments | signatures | Copy To.
    result = _pf(stats['is_pass'])
    comment = (report.comments or '').strip()
    summary = f'Overall: {S._fmt(overall)} points / 100 sq yds — {result}' if overall is not None else ''
    comments = _m(f'<b>COMMENTS</b><br/>{comment}<br/>{summary}', NOTE)
    copy_to = _m('<b>Copy To :</b><br/>1. MD<br/>2. PM<br/>3. Merchandiser<br/>4. SO', NOTE)
    sig_w = (pw - 110*mm - 40*mm) / 3
    sig = lambda label: _box([[_m('', NOTE)], [_m(label, CAP)]], [sig_w], heights=[12*mm, 6*mm], grid=False,
                             cmds=[('LINEABOVE', (0, 1), (-1, 1), 0.6, LINE), ('ALIGN', (0, 0), (-1, -1), 'CENTER')])
    story += [_box([[comments, sig('Quality Inspector'), sig('Cutting Incharge'), sig('QAM'), copy_to]],
                   [110*mm, sig_w, sig_w, sig_w, 40*mm], cmds=[('VALIGN', (0, 0), (-1, -1), 'TOP')])]

    # Not on the paper form, kept so no saved data is lost.
    decided = [r for r in rolls if (r.disposition or 'pending') != 'pending' or r.shade_band]
    located = [(r, d) for r in rolls for d in r.defects if d.position_yard is not None or d.side]
    if decided or located:
        story += [PageBreak(), S._section_title('ROLL DECISIONS & DEFECT LOCATIONS', landscape_page=True), Spacer(1, 2*mm)]
        if decided:
            rows = [['Roll No', 'Decision', 'Shade Band', 'Note', 'Inspector']] + [
                [r.roll_no, (r.disposition or 'pending').upper(), r.shade_band or '', r.disposition_note or '', r.inspector_name or '']
                for r in decided]
            story += [S._table(rows, widths=[22*mm, 26*mm, 22*mm, pw - 130*mm, 60*mm], header_rows=1, font=6.5), Spacer(1, 3*mm)]
        if located:
            located.sort(key=lambda rd: (rd[0].roll_no, float(rd[1].position_yard) if rd[1].position_yard is not None else 1e9))
            rows = [['Roll No', f'Position ({qu.lower()})', 'Side', 'Defect', 'Size', 'Points', 'Photo']] + [
                [r.roll_no, S._fmt(d.position_yard), (d.side or '').title(), d.defect_type_name or '', d.range_label or '',
                 d.points_snapshot, 'Yes' if d.photo_url else ''] for r, d in located[:400]]
            story += [S._table(rows, widths=[22*mm, 22*mm, 18*mm, pw - 152*mm, 40*mm, 18*mm, 32*mm], header_rows=1, font=6.2)]

    # Photos below the score sheet: the PLM fabric photo, then defect photos.
    fabric = _photo(db, 'fabric_inspection_plm', plm.id, (pw - 40*mm, 110*mm))
    photos = S._defect_photo_evidence(report, pw)
    if fabric is not None:
        story += [PageBreak(), S._section_title('FABRIC PHOTO', landscape_page=True), Spacer(1, 2*mm),
                  _photo_box(fabric, f'{_colors(plm, [r.colour_code for r in rolls])} — PLM {plm.plm_code}', (pw - 40*mm, 110*mm), pw)]
    if photos:
        story += photos
    if fabric is None and not photos:
        story += [Spacer(1, 4*mm), S._section_title('FABRIC / DEFECT PHOTOS', landscape_page=True), Spacer(1, 2*mm),
                  _m('No photos were captured for this 4-point inspection.', S.SMALL)]
    return story, True


# --------------------------------------------------------------------------------------------
# SKEW & BOWING — one page Before Wash, one page After Wash (paper pages 4-5)
# --------------------------------------------------------------------------------------------

_BUCKETS = (('0-0.5"', 0.5), ('1"', 1.0), ('1.5"', 1.5), ('2"', 2.0), ('2.5"', 2.5), ('3"', 3.0), ('3.5"', 3.5), ('4"', 4.0))


def _bucket(amount_inch):
    for i, (_, limit) in enumerate(_BUCKETS):
        if amount_inch <= limit + 1e-9:
            return i
    return len(_BUCKETS)


def skew_bowing(db, plm):
    from app.features.skew_bowing import models as sk

    r = db.query(sk.SkewBowingReport).options(joinedload(sk.SkewBowingReport.rolls)).filter(sk.SkewBowingReport.plm_id == plm.id).first()
    if not r:
        return None
    pw = _w(False)
    std = float(r.standard_point_snapshot) if r.standard_point_snapshot is not None else None
    mu = r.measurement_unit_snapshot or 'inch'
    rolls = sorted(r.rolls, key=lambda x: x.created_at or 0)
    story = []
    for after in (False, True):
        stage = 'AFTER WASH' if after else 'BEFORE-WASH'
        rows = [x for x in rolls if (skew_service.roll_after_percent(x) is not None if after else skew_service.roll_before_percent(x) is not None)]
        if after and not rows and story:
            continue
        if story:
            story.append(PageBreak())
        doc_box = _box([[_m('ISML-02/QAD<br/>Version No : 01<br/>Doc No : 05', NOTE)]], [36*mm])
        title = _box([[_m(f'<b>{stage}</b>', MID)], [_m('Interfab Shirt Manufacturing Limited', MID)],
                      [_m('Skew &amp; Bowing Check report', S.SUBTITLE)]], [pw - 70*mm], grid=False)
        story += [_box([[_logo(), title, doc_box]], [34*mm, pw - 70*mm, 36*mm], grid=False,
                       cmds=[('ALIGN', (0, 0), (-1, -1), 'CENTER')])]
        story += [_kv_rows([
            ('Buyer:', _buyer(plm)), ('Date:', S._date(r.report_date)),
            ('PLM/Style:', plm.plm_code), ('Color:', _colors(plm, [x.colour_code for x in rolls])),
            ('SUPPLIER:', _supplier(plm)), ('INHOUSE DATE:', S._date(r.in_house_date or plm.received_at)),
            ('Receive Qty:', f'{S._fmt(r.receive_qty)} {(r.quantity_unit_snapshot or "").upper()}'), ('Fabric Construction:', r.fabric_construction or ''),
            ('TOTAL ROLL:', r.total_roll or _total_rolls(plm)), ('CHECK ROLL:', len(rows)),
        ], pw, label_w=32*mm)]
        swatch = _photo(db, 'skew_after_wash' if after else 'skew_before_wash', r.id, (70*mm, 34*mm),
                        fallback_types=() if after else ('skew_bowing_report',),
                        fallback_roll_type=None if after else 'skew_bowing_roll', roll_ids=[x.id for x in rolls])
        acc = [[_m('Acceptance', MID), '', '', _m('STD', CAP)],
               [_m('Fabric Width', CAP), _m('Solids', CAP), _m('Stripes', CAP), '']] + \
              [[_m(a, KV), _m(b, KV), _m(c, KV), ''] for a, b, c in skew_service.ACCEPTANCE_TABLE]
        acc[2][3] = _m(f'<b>{S._fmt(std)}%</b>' if std is not None else '', KV)
        acc_t = _box(acc, [26*mm, 22*mm, 22*mm, 18*mm], cmds=[('SPAN', (0, 0), (2, 0)), ('SPAN', (3, 2), (3, 4)),
                                                              ('ALIGN', (0, 0), (-1, -1), 'CENTER')])
        story += [_box([[_m('FABRIC SWATCH', CAP), _m('', CAP)],
                        [swatch or _m('No image uploaded', S.SMALL), acc_t]], [pw - 92*mm, 92*mm],
                       cmds=[('ALIGN', (0, 0), (-1, -1), 'CENTER')])]
        # Bucket grid: amount per roll placed under its size column.
        story += [_m('<b>Skew &amp; Bowing Check report</b>', ParagraphStyle('c', parent=CAP, fontSize=9))]
        head = ['ROLL NO'] + [b for b, _ in _BUCKETS] + ['Over 4"', 'Result %', 'Pass/Fail']
        grid = [head]
        lines = []
        for x in rows:
            if after:
                amount = abs(float(x.ac_value_after) - float(x.bd_value_after))
                pct = skew_service.roll_after_percent(x)
                lines.append(f'Roll {x.roll_no}: 2 × ({S._fmt(x.ac_value_after)} − {S._fmt(x.bd_value_after)}) ÷ '
                             f'({S._fmt(float(x.ac_value_after) + float(x.bd_value_after))}) × 100 = {S._fmt(pct)}%')
            elif x.length_before is not None and x.variation_before is not None:
                amount = abs(float(x.variation_before))
                pct = skew_service.roll_before_percent(x)
                lines.append(f'Roll {x.roll_no}: {S._fmt(x.variation_before)} ÷ {S._fmt(x.length_before)} × 100 = {S._fmt(pct)}%')
            else:  # older rolls saved before-wash AC/BD
                amount = abs(float(x.ac_value_before or 0) - float(x.bd_value_before or 0))
                pct = skew_service.roll_before_percent(x)
                lines.append(f'Roll {x.roll_no}: AC {S._fmt(x.ac_value_before)} / BD {S._fmt(x.bd_value_before)} = {S._fmt(pct)}%')
            inch = amount / 2.54 if mu == 'cm' else amount
            cells = [''] * (len(_BUCKETS) + 1)
            cells[_bucket(inch)] = S._fmt(amount)
            grid.append([x.roll_no, *cells, f'{S._fmt(pct)}%' if pct is not None else '', _pf(skew_service.roll_is_pass(pct, std))])
        for _ in range(max(0, 8 - len(rows))):
            grid.append([''] * len(head))
        gw = [20*mm] + [(pw - 20*mm - 40*mm) / (len(_BUCKETS) + 1)] * (len(_BUCKETS) + 1) + [20*mm, 20*mm]
        story += [Spacer(1, 2*mm), S._table(grid, widths=gw, header_rows=1, font=7,
                                            row_heights=[None] * (len(rows) + 1) + [6*mm] * (len(grid) - len(rows) - 1))]
        stats = skew_service.compute_report_stats(rows, std)
        avg = stats['avg_percent_after' if after else 'avg_percent_before']
        ok = stats['is_pass_after' if after else 'is_pass_before']
        unit_note = f'Values in {mu}. Result ≤ STD = PASS.'
        comments = _m('<b>COMMENTS / Action Plan</b><br/>' + '<br/>'.join(lines) + (f'<br/>{r.comments_action_plan}' if r.comments_action_plan else '')
                        + f'<br/><br/><b>AVG POINT: {S._fmt(avg)}% | RESULT: {_pf(ok)}</b><br/>{unit_note}', NOTE)
        sigs = _signatures(['QC', 'QA Inch/Manager'], pw / 2 - 10)
        story += [_box([[comments, sigs]], [pw / 2, pw / 2], cmds=[('VALIGN', (0, 0), (-1, -1), 'TOP')])]
    return story, False


# --------------------------------------------------------------------------------------------
# GSM — report table, then Before / After photo pages (paper pages 6-8)
# --------------------------------------------------------------------------------------------

def gsm(db, plm):
    from app.features.gsm import models as gm, service as gs

    r = db.query(gm.GsmCheckReport).options(joinedload(gm.GsmCheckReport.rolls)).filter(gm.GsmCheckReport.plm_id == plm.id).first()
    if not r:
        return None
    pw = _w(False)
    std = float(r.standard_gsm or 0)
    std_after = gs.required_after(r)
    tol = float(r.tolerance_snapshot) if r.tolerance_snapshot is not None else None
    stats = gs.compute_report_stats(r.rolls, std, tol, std_after)
    rolls = sorted(r.rolls, key=lambda x: x.created_at or 0)
    color = _colors(plm, [x.color for x in rolls] + [r.color])

    def header(sub):
        return [
            _box([[_logo(8*mm), _m('Interfab Shirt Manufacturing Ltd.', BIG)]], [30*mm, pw - 30*mm],
                 cmds=[('SPAN', (0, 0), (0, 0))]),
            _box([[_m(sub, MID)]], [pw], cmds=[('ALIGN', (0, 0), (-1, -1), 'CENTER')]),
            _box([[_m(f'<b>Date:</b> {S._date(r.report_date)}', KV), _m(f'<b>Tolerance:</b> ±{S._fmt(tol)}%' if tol is not None else '', KV)]], [pw / 2, pw / 2]),
            _box([[_m('<b>Buyer:</b>', KV), _m(_buyer(plm), KV), _m('<b>PLM:</b>', KV), _m(plm.plm_code, KV), _m(f'<b>COLOR:</b> {color}', KV)]],
                 [18*mm, 40*mm, 16*mm, 40*mm, pw - 114*mm]),
        ]

    def side(after):
        req = std_after if after else std
        head = ['Roll No.', 'Require GSM', 'Actual GSM', 'Over/ Less %', 'Remarks']
        rows = [head]
        for x in rolls:
            actual = x.actual_gsm_after if after else x.actual_gsm_before
            if actual is None:
                continue
            rows.append([x.roll_no, S._fmt(req), S._fmt(actual), S._fmt(gs.over_less_pct(float(actual), req)),
                         (x.remarks_after if after else x.remarks_before) or ''])
        avg = stats['avg_over_less_after_pct' if after else 'avg_over_less_before_pct']
        ok = stats['pass_after' if after else 'pass_before']
        n = len(rows)
        rows.append([f'AVG GSM = {S._fmt(avg)}%', '', '', '', ''])
        rows.append([f'GSM REPORT {_pf(ok)}' if ok is not None else 'GSM REPORT —', '', '', '', ''])
        w = (pw / 2) - 1*mm
        return S._table(rows, widths=[w * f for f in (0.18, 0.2, 0.2, 0.22, 0.2)], header_rows=1, font=7.5,
                        spans=[('SPAN', (0, n), (-1, n)), ('SPAN', (0, n + 1), (-1, n + 1))],
                        extra_cmds=[('FONTNAME', (0, n), (-1, n + 1), 'Helvetica-Bold')])

    story = header('GSM Check Report')
    story += [_box([[_m('BEFORE WASH', CAP), _m('AFTER WASH', CAP)], [side(False), side(True)]], [pw / 2, pw / 2],
                   grid=False, cmds=[('VALIGN', (0, 0), (-1, -1), 'TOP'), ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                                     ('LEFTPADDING', (0, 0), (-1, -1), 1), ('RIGHTPADDING', (0, 0), (-1, -1), 1)])]

    for after in (False, True):
        photo = _photo(db, 'gsm_after_wash' if after else 'gsm_before_wash', r.id, (pw - 20*mm, 150*mm),
                       fallback_roll_type=None if after else 'gsm_roll', roll_ids=[x.id for x in rolls])
        req = std_after if after else std
        band = _box([[_m('REQ GSM:', KVB), _m(S._fmt(req), KV)],
                     [_m(f'+{S._fmt(tol)}%=', KVB), _m(S._fmt(req * (1 + tol / 100)) if tol is not None else '', KV)],
                     [_m(f'-{S._fmt(tol)}%=', KVB), _m(S._fmt(req * (1 - tol / 100)) if tol is not None else '', KV)]],
                    [24*mm, 26*mm])
        labels = [['Roll No', 'GSM']] + [[x.roll_no, S._fmt(x.actual_gsm_after if after else x.actual_gsm_before)]
                                          for x in rolls if (x.actual_gsm_after if after else x.actual_gsm_before) is not None]
        story += [PageBreak(), *header(f'GSM Check Report — {"AFTER WASH" if after else "BEFORE WASH"}'), Spacer(1, 3*mm),
                  _box([[S._table(labels, widths=[25*mm, 25*mm], header_rows=1, font=7.5), band]], [pw - 55*mm, 55*mm],
                       grid=False, cmds=[('VALIGN', (0, 0), (-1, -1), 'TOP'), ('ALIGN', (1, 0), (1, 0), 'RIGHT')]),
                  Spacer(1, 3*mm),
                  _photo_box(photo, f'{color} — {"AFTER" if after else "BEFORE"} WASH GSM SWATCH', (pw - 20*mm, 150*mm), pw)]
    return story, False


# --------------------------------------------------------------------------------------------
# MOISTURE — check list with acceptable standards, then Before / After meter photos
# --------------------------------------------------------------------------------------------

def moisture(db, plm):
    from app.features.moisture import models as mm_

    r = db.query(mm_.MoistureCheckReport).options(joinedload(mm_.MoistureCheckReport.rolls)).filter(mm_.MoistureCheckReport.plm_id == plm.id).first()
    if not r:
        return None
    pw = _w(False)
    acc = float(r.acceptance_snapshot) if r.acceptance_snapshot is not None else None
    rolls = sorted(r.rolls, key=lambda x: x.created_at or 0)
    color = _colors(plm, [x.color for x in rolls])
    swatch = _photo(db, 'moisture_before_wash', r.id, (40*mm, 22*mm))
    story = [_box([[_m(r.factory or 'Interfab Shirt Mfg. Ltd.', KVB), _m('<i>Moisture Check List</i>', BIG)]], [60*mm, pw - 60*mm],
                  cmds=[('BACKGROUND', (0, 0), (0, 0), HEAD), ('ALIGN', (1, 0), (1, 0), 'CENTER')], heights=[14*mm]),
             Spacer(1, 2*mm)]
    info = [
        [_m('Buyer', KVB), _m(_buyer(plm), KV), _m('Style/PLM', KVB), _m(plm.plm_code, KV)],
        [_m('Vendor', KVB), _m(r.vendor or _supplier(plm), KV), _m('Acceptance', KVB), _m(f'{S._fmt(acc)}%' if acc is not None else '', KV)],
        [_m('Factory', KVB), _m(r.factory or 'Interfab Shirt Mfg. Ltd.', KV), _m('Swatch', KVB), swatch or _m('', KV)],
        [_m('Date', KVB), _m(S._date(r.report_date), KV), _m('Time:', KVB), _m(r.report_time or '', KV)],
    ]
    story += [_box(info, [30*mm, pw / 2 - 30*mm, 30*mm, pw / 2 - 30*mm], heights=[None, None, 26*mm, None])]
    rows = [['Roll No', 'Fabric composition', 'Color', 'Rating', 'Pass/Fail', 'Remarks']]
    for after in (False, True):
        for x in rolls:
            v = x.swatch_rating_after if after else x.swatch_rating_before
            if v is None:
                continue
            rows.append([x.roll_no, x.fabric_composition or plm.fabric_composition or '', S._color_for(plm, x.roll_no, x.color, S._planned_color_map(plm)),
                         f'{S._fmt(v)}%', _pf(float(v) <= acc if acc is not None else None), 'AFTER WASH' if after else 'BEFORE WASH'])
        if not after:
            rows.append([''] * 6)
    story += [S._table(rows, widths=[18*mm, 50*mm, 32*mm, 22*mm, 22*mm, pw - 144*mm], header_rows=1, font=7.5)]
    std_rows = [['Fabric Composition', 'Maximum Moisture Index Level (100% Scale)']] + \
               [[' / '.join(a.title() for a in aliases[:2]), S._fmt(v)] for aliases, v in FIBER_LIMITS]
    story += [Spacer(1, 3*mm), _m('<b><u>Acceptable Standards:</u></b>', ParagraphStyle('as', parent=MID, alignment=TA_CENTER)),
              S._table(std_rows, widths=[pw * 0.6, pw * 0.4], header_rows=1, font=7.2), Spacer(1, 2*mm),
              _m('<b>Notes:</b> All moisture readings exceeding the acceptable level should be re-dried till standard level is met. '
                   'Long stacked cartons (3 days or more) should be checked for moisture too until loaded for shipping out.', NOTE),
              _m('<b>Remarks:</b> For fabric blends: ML(Blend) = ML(Fibre A) × A% / 100 + ML(Fibre B) × B% / 100, rounded '
                   '(.50 and above goes up).', NOTE), Spacer(1, 3*mm), _signatures(['QC', 'BTM / Head Of QA'], pw)]
    for after in (False, True):
        photo = _photo(db, 'moisture_after_wash' if after else 'moisture_before_wash', r.id, (pw - 20*mm, 200*mm))
        story += [PageBreak(), _box([[_m(f'Moisture Meter Reading — {"AFTER" if after else "BEFORE"} WASH', MID)]], [pw],
                                    cmds=[('ALIGN', (0, 0), (-1, -1), 'CENTER')]), Spacer(1, 3*mm),
                  _photo_box(photo, f'{color} — {"AFTER" if after else "BEFORE"}', (pw - 20*mm, 200*mm), pw)]
    return story, False


# --------------------------------------------------------------------------------------------
# RELAXATION (paper pages 14-15)
# --------------------------------------------------------------------------------------------

def relaxation(db, plm):
    from app.features.relaxation import models as rm, service as rs

    r = db.query(rm.FabricRelaxationReport).options(joinedload(rm.FabricRelaxationReport.rolls)).filter(rm.FabricRelaxationReport.plm_id == plm.id).first()
    if not r:
        return None
    pw = _w(False)
    rolls = sorted(r.rolls, key=lambda x: x.created_at or 0)
    color = _colors(plm, [x.color_code for x in rolls] + [r.color])
    # Relaxation time is typed in the app for this fabric; blank = not printed.
    hours = r.relaxation_hours_snapshot
    story = [_box([[_m('FABRIC RELAXATION REPORT', MID)]], [pw], cmds=[('ALIGN', (0, 0), (-1, -1), 'CENTER')]),
             _box([[_m(f'<b>BUYER:</b> {_buyer(plm)}', KV), _m(f'<b>STYLE/PLM:</b> {plm.plm_code}', KV),
                    _m(f'<b>COLOR:</b> {color}', KV), _m(f'<b>DATE:</b> {S._first_date(*[x.roll_date for x in rolls], plm.plm_date)}', KV)]],
                  [pw * 0.22, pw * 0.26, pw * 0.32, pw * 0.2])]
    h1 = ['FABRIC INFORMATION', '', '', 'BEFORE RELAXATION', '', '', 'AFTER RELAXATION', '', '', 'SHRINKAGE %', '', '']
    h2 = ['COLOR CODE', 'ROLL NO', f'FABRIC {S._qty_unit(plm)}', 'START TIME', 'LENGTH', 'WIDTH',
          f'RELAXATION TIME AFTER {hours} HOURS' if hours else 'RELAXATION TIME', 'LENGTH', 'WIDTH', 'LENGTH', 'WIDTH', 'REMARK']
    rows = [h1, h2]

    def pct(v):
        return f'{S._fmt(v)}%' if v is not None else ''

    # Paper layout: roll + before relaxation on the first line, then one line per reading.
    for x in rolls:
        reads = rs.readings(x) or [{}]
        for i, rd in enumerate(reads):
            first = i == 0
            rows.append([(x.color_code or '') if first else '', x.roll_no if first else '', S._fmt(x.fabric_yds_mtr) if first else '',
                         (x.before_relax_start_time or '') if first else '', S._fmt(x.before_relax_length) if first else '',
                         S._fmt(x.before_relax_width) if first else '', rd.get('time') or '', S._fmt(rd.get('length')),
                         S._fmt(rd.get('width')), pct(rd.get('shrinkage_length_pct')), pct(rd.get('shrinkage_width_pct')),
                         (x.remark or '') if first else ''])
    filled = len(rows) - 2
    for _ in range(max(0, 14 - filled)):
        rows.append([''] * 12)
    story += [S._table(rows, widths=S._fit_col_widths([20, 14, 16, 16, 14, 14, 20, 14, 14, 15, 15, 18], pw), header_rows=2, font=6.6,
                       row_heights=[None] * (filled + 2) + [6*mm] * (len(rows) - filled - 2),
                       spans=[('SPAN', (0, 0), (2, 0)), ('SPAN', (3, 0), (5, 0)), ('SPAN', (6, 0), (8, 0)), ('SPAN', (9, 0), (11, 0))])]
    photo = _photo(db, 'relaxation_report', r.id, (pw - 60*mm, 45*mm), fallback_roll_type='relaxation_roll', roll_ids=[x.id for x in rolls])
    story += [_photo_box(photo, 'FABRIC SWATCH', (pw - 60*mm, 45*mm), pw),
              _box([[_m('<b>COMMENTS</b>' + (f'<br/>RELAXATION TIME {hours} HOURS.' if hours else '') + (f'<br/>{r.comments}' if r.comments else ''), NOTE)]], [pw]),
              _signatures(['PO OFFICER', 'QA OFFICER', 'CUTTING MANAGER'], pw)]
    return story, False


# --------------------------------------------------------------------------------------------
# SHADE CHECK (paper page 16)
# --------------------------------------------------------------------------------------------

def shade_check(db, plm):
    from app.features.shade_check import models as sm

    r = (db.query(sm.ShadeCheckReport)
         .options(joinedload(sm.ShadeCheckReport.rolls).joinedload(sm.ShadeCheckRoll.results))
         .filter(sm.ShadeCheckReport.plm_id == plm.id).first())
    if not r:
        return None
    pw = _w(True)
    points = sorted(r.points_snapshot or [], key=lambda p: int(p.get('sort_order', 0)))
    length = [p for p in points if p.get('check_type') == 'length']
    width = [p for p in points if p.get('check_type') == 'width']
    rolls = sorted(r.rolls, key=lambda x: x.created_at or 0)
    swatch = _photo(db, 'shade_check_report', r.id, (40*mm, 16*mm), fallback_roll_type='shade_check_roll', roll_ids=[x.id for x in rolls])
    story = [_box([[_logo(12*mm), _m('Fabric Shade Check Report', BIG), swatch or _m('', KV)]], [40*mm, pw - 90*mm, 50*mm],
                  cmds=[('ALIGN', (0, 0), (-1, -1), 'CENTER')], heights=[20*mm]),
             _box([[_m('<b>Buyer:</b>', KV), _m(_buyer(plm), KV), _m('<b>PLM/Style:</b>', KV), _m(plm.plm_code, KV)]],
                  [24*mm, pw / 2 - 24*mm, 28*mm, pw / 2 - 28*mm])]
    h1 = ['Date', 'Colour', 'Cons', 'Roll No.'] + (['Length Wise'] + [''] * (len(length) - 1) if length else []) + \
         [''] + (['Width Wise'] + [''] * (len(width) - 1) if width else []) + ['Remarks']
    h2 = ['', '', '', ''] + [p.get('label', '') for p in length] + ['Position'] + [p.get('label', '') for p in width] + ['']
    rows = [h1, h2]
    spans = [('SPAN', (c, 0), (c, 1)) for c in range(4)] + [('SPAN', (len(h1) - 1, 0), (len(h1) - 1, 1))]
    if length:
        spans.append(('SPAN', (4, 0), (3 + len(length), 0)))
    wcol = 4 + len(length) + 1
    if width:
        spans.append(('SPAN', (wcol, 0), (wcol + len(width) - 1, 0)))
    ok = lambda v: 'OK' if v is True else 'NOT OK' if v is False else ''
    planned = S._planned_color_map(plm)
    for roll in rolls:
        by = {x.point_key_snapshot: x for x in roll.results}
        start = len(rows)
        for i, pos in enumerate(('FIRST', 'MIDDLE', 'LAST')):
            row = [S._first_date(roll.roll_date, plm.plm_date) if i == 0 else '', S._color_for(plm, roll.roll_no, roll.colour, planned) if i == 0 else '',
                   S._fmt(roll.cons) if i == 0 else '', roll.roll_no if i == 0 else '']
            row += [ok(getattr(by.get(p.get('check_key')), 'first_ok', None)) if i == 0 else '' for p in length]
            row += [pos]
            attr = ('first_ok', 'middle_ok', 'last_ok')[i]
            row += [ok(getattr(by.get(p.get('check_key')), attr, None)) for p in width]
            row += [roll.remarks or '' if i == 0 else '']
            rows.append(row)
        for c in list(range(0, 4 + len(length))) + [len(h1) - 1]:
            spans.append(('SPAN', (c, start), (c, start + 2)))
    weights = [16, 20, 12, 14] + [16] * len(length) + [14] + [14] * len(width) + [26]
    story += [S._table(rows, widths=S._fit_col_widths(weights, pw), header_rows=2, font=6.8, spans=spans)]
    if r.comments:
        story += [Spacer(1, 2*mm), _box([[_m(f'<b>COMMENTS</b><br/>{r.comments}', NOTE)]], [pw])]
    return story, True


# --------------------------------------------------------------------------------------------
# FACTORY REPORT (paper page 17) — Mill on the left, Factory on the right
# --------------------------------------------------------------------------------------------

def factory_report(db, plm):
    from app.features.factory_report import routes as fr
    from app.features.fabric_inspection.service import length_to_yards

    report = fr._ensure_derived(db, plm)
    if not report:
        return None
    pw = _w(True)
    planned = [(r, c) for c in plm.colors for r in c.rolls]
    mu = plm.measurement_unit.value if hasattr(plm.measurement_unit, 'value') else str(plm.measurement_unit)
    mill_points = sum(float(r.supplier_total_point or 0) for r, _ in planned)
    area = 0.0
    mill_len = 0.0
    mill_w = []
    for r, _ in planned:
        if r.marked_length and r.marked_width:
            mill_len += float(r.marked_length)
            mill_w.append(float(r.marked_width))
            area += width_to_inches(float(r.marked_width), mu) * length_to_yards(float(r.marked_length), plm.quantity_unit)
    mill_avg = mill_points * 3600 / area if area else None
    std = float(report.standard_snapshot) if report.standard_snapshot is not None else None
    src = S._four_point_for_export(db, plm.id)
    fw = [float(x.width_actual) for x in (src.rolls if src else []) if x.width_actual]
    qu = S._qty_unit(plm)
    codes = _colors(plm, [report.color])

    def panel(*, yardage, pieces, widths, total, avg, pct, stage, date_text, ok):
        return _box([
            [_m('<b><i>Customer</i></b>', KV), _m(f'<b>{_buyer(plm)}</b>', KV), _m('Requirement:', KV), ''],
            [_m('Job No.', KV), _m(report.job_no or plm.plm_code, KV), '', ''],
            [_m('Construction', KV), _m(report.construction or plm.fabric_composition or '', KV), '', ''],
            [_m('Color', KV), _m(report.color or codes, KV), '', ''],
            [_m('Order Qty', KV), _m(f'{S._fmt(report.order_qty)} {qu}', KV), '', ''],
            [_m('<b><i>Inspection Status</i></b>', KV), '', '', ''],
            [_m('Inspection Yardage', KV), _m(f'{S._fmt(yardage)} {qu}', KV), _m('Inspection Date', KV), _m(date_text, KV)],
            [_m('Inspection Pieces', KV), _m(f'{pieces} Rolls', KV), _m('Inspection Percentage', KV), _m(f'{S._fmt(pct)}%' if pct is not None else '', KV)],
            [_m('Actual Width', KV), _m(S._range_text(widths), KV), _m('Inspection Stage', KV), _m(stage, KV)],
            [_m('Total Point', KV), _m(S._fmt(total), KV), '', ''],
            [_m('<b><i>Result</i></b>', KV), _m('Actual', CAP), _m('Standard', CAP), _m('Pass/Fail', CAP)],
            [_m('Average Shipment Point', KV), _m(S._fmt(avg), KV), _m(S._fmt(std), KV), _m(_pf(ok), KV)],
        ], [pw / 2 * f for f in (0.3, 0.27, 0.25, 0.18)], grid=False,
            cmds=[('BOX', (0, 0), (-1, 4), 0.6, LINE), ('BOX', (0, 6), (-1, 9), 0.6, LINE), ('BOX', (0, 10), (-1, 11), 0.6, LINE),
                  ('TOPPADDING', (0, 0), (-1, -1), 1.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5)])

    mill_ok = (mill_avg <= std) if mill_avg is not None and std is not None else None
    mill = panel(yardage=mill_len or plm.allocated_quantity, pieces=len(planned), widths=mill_w, total=mill_points, avg=mill_avg,
                 pct=100.0 if mill_len else None, stage='Final', date_text='', ok=mill_ok)
    fact = panel(yardage=report.inspection_yardage, pieces=report.inspection_pieces or 0, widths=fw, total=report.total_point,
                 avg=report.avg_shipment_point, pct=report.inspection_percentage, stage=report.inspection_stage or 'Final',
                 date_text=S._date(report.inspection_date), ok=report.is_pass)
    story = [_box([[_m(f'<b><u><i>{_supplier(plm).upper()}</i></u></b>', MID), _m('Factory Report', BIG)]], [pw / 2, pw / 2], grid=False,
                  cmds=[('BOX', (1, 0), (1, 0), 0.8, LINE), ('ALIGN', (1, 0), (1, 0), 'CENTER')]), Spacer(1, 3*mm),
             _box([[_m('<b>MILL (Supplier packing list)</b>', CAP), _m('<b>FACTORY (4-point inspection)</b>', CAP)], [mill, fact]],
                  [pw / 2, pw / 2], grid=False, cmds=[('VALIGN', (0, 0), (-1, -1), 'TOP')])]
    return story, True


# --------------------------------------------------------------------------------------------
# CORRELATION (paper page 18) — Mill and Factory side by side, defect rows per roll
# --------------------------------------------------------------------------------------------

def correlation(db, plm):
    from app.features.correlation import models as cm, routes as cr, service as cs

    report = cr._ensure_report(db, plm)
    cr._sync_from_sources(db, report, plm)
    db.flush()
    report = db.query(cm.FabricCorrelationReport).options(
        joinedload(cm.FabricCorrelationReport.rolls).joinedload(cm.FabricCorrelationRoll.defects)).get(report.id)
    pw = _w(True)
    qu = (report.quantity_unit_snapshot or 'yds').upper()
    mu = (report.measurement_unit_snapshot or 'inch').upper()
    story = [_m('Interfab Shirt Manufacturing Ltd. Fabric Correlation report', BIG)]
    head0 = ['Mill Report', '', '', '', '', '', '', '', '', '', '', f'PLM/STY: {plm.plm_code}', '', f'COL: {_colors(plm)}', '', '',
             'Factory Report', '', '', '', '', '', '', '']
    head = ['Roll No', 'DEFECT POINT', f'Length ({qu})', f'Width ({mu})', '1', '2', '3', '4', 'Total Point', 'Average Point', 'Pass/Fail',
            'Roll No', 'DEFECT POINT', f'Length ({qu})', f'Width ({mu})', '1', '2', '3', '4', 'Total Point', 'Average Point',
            'Standard', 'SCS', 'Pass/Fail']
    rows = [head]
    spans = []
    for x in sorted(report.rolls, key=lambda z: z.roll_no):
        f = cs.compute_roll_fields(x, report.measurement_unit_snapshot, report.quantity_unit_snapshot)
        by_type: OrderedDict = OrderedDict()
        for d in x.defects:
            if int(d.count or 0) <= 0:
                continue
            slot = by_type.setdefault(d.defect_type_name_snapshot or 'Defect', [0, 0, 0, 0])
            pts = int(round(float(d.points_snapshot or 0) / max(int(d.count), 1)))
            if 1 <= pts <= 4:
                slot[pts - 1] += int(d.count)
        lines = list(by_type.items()) or [('No defect', [0, 0, 0, 0])]
        start = len(rows)
        for i, (name, counts) in enumerate(lines):
            mill = [x.roll_no, 'Supplier Total', S._fmt(x.length_yards), S._fmt(x.width), '', '', '', '',
                    S._fmt(x.mill_total_point), S._fmt(f['mill_average_point']), _pf(f['mill_pass'])] if i == 0 else [''] * 11
            fac = [x.roll_no if i == 0 else '', name, S._fmt(x.factory_length) if i == 0 else '', S._fmt(x.factory_width) if i == 0 else '',
                   *[str(c) if c else '' for c in counts],
                   *( [S._fmt(x.factory_total_points), S._fmt(f['factory_average_point']), S._fmt(x.standard), x.scs, _pf(f['factory_pass'])]
                      if i == 0 else [''] * 5)]
            rows.append(mill + fac)
        end = len(rows) - 1
        if end > start:
            for c in list(range(0, 11)) + [11, 13, 14, 19, 20, 21, 22, 23]:
                spans.append(('SPAN', (c, start), (c, end)))
    weights = [10, 16, 11, 10, 6, 6, 6, 6, 10, 11, 11, 10, 22, 11, 10, 6, 6, 6, 6, 10, 11, 10, 7, 11]
    story += [_box([[_m('<b>Mill Report</b>', MID), _m(f'<b>PLM/STY:</b> {plm.plm_code}', KV), _m(f'<b>COL:</b> {_colors(plm)}', KV),
                     _m('<b>Factory Report</b>', MID)]], [pw * 0.4, pw * 0.12, pw * 0.2, pw * 0.28],
                   cmds=[('ALIGN', (0, 0), (-1, -1), 'CENTER')]),
              S._table(rows, widths=S._fit_col_widths(weights, pw), header_rows=1, font=5.8, spans=spans,
                       extra_cmds=[('LINEAFTER', (10, 0), (10, -1), 1.4, LINE)])]
    return story, True


# --------------------------------------------------------------------------------------------
# SHRINKAGE & PATTERN ADJUSTMENT (paper page 19)
# --------------------------------------------------------------------------------------------

def shrinkage(db, plm):
    from app.features.shrinkage import models as shm, service as shs

    r = (db.query(shm.ShrinkagePatternReport)
         .options(joinedload(shm.ShrinkagePatternReport.rolls).joinedload(shm.ShrinkageRoll.measurements))
         .filter(shm.ShrinkagePatternReport.plm_id == plm.id).first())
    if not r:
        return None
    pw = _w(True)
    rolls = sorted(r.rolls, key=lambda x: x.created_at or 0)
    story = [_box([[_m('SHRINKAGE &amp; PATTERN ADJUSTMENT REPORT', BIG)]], [pw], cmds=[('ALIGN', (0, 0), (-1, -1), 'CENTER')]),
             _box([[_m(f'<b>CON:</b> {plm.consignment or ""}', KV), _m('<b>WASH</b>', KV), _m(f'<b>SEASON:</b> {r.season or ""}', KV),
                    _m(f'<b>CENTRIC NO:</b> {plm.plm_code}', KV), _m(f'<b>COLOR:</b> {r.color or _colors(plm)}', KV),
                    _m(f'<b>QC:</b> {r.qc_name or ""}', KV), _m(f'<b>DATE:</b> {S._date(r.report_date)}', KV)]],
                  S._fit_col_widths([18, 8, 14, 16, 24, 12, 12], pw))]
    h1 = ['SERIAL NO', 'ROLL NO', S._qty_unit(plm), 'BEFORE WASH', '', 'AFTER WASH', '', 'SHRINKAGE(-)/ELONGATION(+)', '',
          'ADJUST SHRINKAGE %', '', 'AVG %', '', 'PATTERN', 'SHADE']
    h2 = ['', '', '', 'LENGTH', 'WIDTH', 'LENGTH', 'WIDTH', 'LENGTH', 'WIDTH', 'LENGTH', 'WIDTH', 'LENGTH', 'WIDTH', '', '']
    rows = [h1, h2]
    spans = [('SPAN', (c, 0), (c, 1)) for c in (0, 1, 2, 13, 14)] + [('SPAN', (c, 0), (c + 1, 0)) for c in (3, 5, 7, 9, 11)]
    all_sl, all_sw = [], []
    for n, x in enumerate(rolls, 1):
        ms = sorted(x.measurements, key=lambda m: m.reading_order) or [None]
        avg = shs.compute_roll_fields(x, None)
        start = len(rows)
        for i, m in enumerate(ms):
            f = shs.compute_measurement_fields(m) if m is not None else {}
            if f.get('shrink_length') is not None:
                all_sl.append(f['shrink_length'])
            if f.get('shrink_width') is not None:
                all_sw.append(f['shrink_width'])
            pct = lambda v: f'{S._fmt(v)}%' if v is not None else ''
            rows.append([n if i == 0 else '', x.roll_no if i == 0 else '', S._fmt(x.yds) if i == 0 else '',
                         S._fmt(getattr(m, 'before_length', None)), S._fmt(getattr(m, 'before_width', None)),
                         S._fmt(getattr(m, 'after_length', None)), S._fmt(getattr(m, 'after_width', None)),
                         S._fmt(f.get('shrink_length')), S._fmt(f.get('shrink_width')),
                         pct(f.get('adjust_length_pct')), pct(f.get('adjust_width_pct')),
                         pct(avg['avg_length_pct']) if i == 0 else '', pct(avg['avg_width_pct']) if i == 0 else '',
                         x.pattern or '' if i == 0 else '', x.shade or '' if i == 0 else ''])
        if len(rows) - 1 > start:
            for c in (0, 1, 2, 11, 12, 13, 14):
                spans.append(('SPAN', (c, start), (c, len(rows) - 1)))
    story += [S._table(rows, widths=S._fit_col_widths([10, 10, 10, 9, 9, 9, 9, 11, 11, 11, 11, 11, 11, 10, 9], pw), header_rows=2, font=6.4, spans=spans)]
    stats = shs.compute_report_stats(rolls, None)
    sl = sum(all_sl) / len(all_sl) if all_sl else None
    sw = sum(all_sw) / len(all_sw) if all_sw else None
    sign = lambda v: '' if v is None else ('MINUS' if v < 0 else 'PLUS')
    al, aw = stats['avg_shrinkage_length'], stats['avg_shrinkage_width']
    pattern = ', '.join(sorted({x.pattern for x in rolls if x.pattern})) or ''
    pct = lambda v: f'{S._fmt(v)}%' if v is not None else ''
    summary = [['PATTERN', 'SHRINKAGE(-)/ELONGATION(+) AVG%', '', '', '', 'Actual Shrinkage found AVG %', '', '', '', 'FIS Value', ''],
               ['', 'LENGTH', 'WIDTH', 'LENGTH', 'WIDTH', 'LENGTH', 'WIDTH', 'LENGTH', 'WIDTH', 'LENGTH', 'WIDTH'],
               [pattern, sign(sl), sign(sw), S._fmt(sl), S._fmt(sw), sign(al), sign(aw), pct(al), pct(aw), 'NILL', 'NILL']]
    adj = [['PATTERN', 'PATTERN ADJUSTMENT AVG %', '', '', ''], ['', 'LENGTH', 'WIDTH', 'LENGTH', 'WIDTH'],
           [pattern, 'PLUS' if al is not None else '', 'PLUS' if aw is not None else '', pct(abs(al)) if al is not None else '', pct(abs(aw)) if aw is not None else '']]
    story += [Spacer(1, 4*mm),
              S._table(summary, widths=S._fit_col_widths([16, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10], pw), header_rows=2, font=6.6,
                       spans=[('SPAN', (0, 0), (0, 1)), ('SPAN', (1, 0), (4, 0)), ('SPAN', (5, 0), (8, 0)), ('SPAN', (9, 0), (10, 0))]),
              Spacer(1, 3*mm),
              S._table(adj, widths=S._fit_col_widths([16, 10, 10, 10, 10], pw * 0.6), header_rows=2, font=6.6,
                       spans=[('SPAN', (0, 0), (0, 1)), ('SPAN', (1, 0), (4, 0))])]
    return story, True


# --------------------------------------------------------------------------------------------
# MASTER COLOR CONTINUITY CARD (paper page 20)
# --------------------------------------------------------------------------------------------

def color_continuity(db, plm):
    from app.features.color_continuity import models as ccm

    r = db.query(ccm.ColorContinuityCard).options(joinedload(ccm.ColorContinuityCard.rolls)).filter(ccm.ColorContinuityCard.plm_id == plm.id).first()
    if not r:
        return None
    pw = _w(True)
    rolls = sorted(r.rolls, key=lambda x: x.roll_no)
    before = ', '.join(sorted({x.shade_group_before for x in rolls if x.shade_group_before}))
    after = ', '.join(sorted({x.shade_group_after for x in rolls if x.shade_group_after}))
    story = [_box([
        [_logo(8*mm), _m('Master Color Continuity Card', MID), '', ''],
        [_m('<b>Buyer:</b>', KV), _m(_buyer(plm), KV), _m('<b>PLM/Style:</b>', KV), _m(plm.plm_code, KV)],
        [_m('<b>Color:</b>', KV), _m(_colors(plm), KV), _m('<b>Cons/Total Roll:</b>', KV), _m(f'{plm.consignment or ""} / {_total_rolls(plm)} ROLL', KV)],
        [_m(f'<b>Before Wash/Non Wash Shade:</b> {before}', KV), '', _m(f'<b>After Wash Shade:</b> {after}', KV), ''],
    ], [pw * 0.18, pw * 0.32, pw * 0.18, pw * 0.32], cmds=[('SPAN', (1, 0), (3, 0)), ('SPAN', (0, 3), (1, 3)), ('SPAN', (2, 3), (3, 3)),
                                                           ('ALIGN', (1, 0), (1, 0), 'CENTER')])]
    photo = _photo(db, 'color_continuity_card', r.id, (pw - 20*mm, 110*mm))
    story += [Spacer(1, 2*mm), _photo_box(photo, 'COLOR CONTINUITY SWATCHES', (pw - 20*mm, 110*mm), pw), Spacer(1, 3*mm)]
    data = [['Roll No', 'Before Wash / Non Wash Shade', 'After Wash Shade', f'Quantity ({S._qty_unit(plm)})']] + \
           [[x.roll_no, x.shade_group_before or '', x.shade_group_after or '', S._fmt(x.yardage)] for x in rolls]
    total = sum(float(x.yardage or 0) for x in rolls)
    data.append(['TOTAL', '', '', S._fmt(total)])
    story += [S._table(data, widths=S._fit_col_widths([20, 40, 40, 24], pw), header_rows=1, font=7.5,
                       extra_cmds=[('FONTNAME', (0, len(data) - 1), (-1, len(data) - 1), 'Helvetica-Bold')])]
    return story, True


BUILDERS = {
    'fabric-inspection': fabric_inspection,
    'skew-bowing': skew_bowing,
    'gsm': gsm,
    'moisture': moisture,
    'relaxation': relaxation,
    'shade-check': shade_check,
    'factory-report': factory_report,
    'correlation': correlation,
    'shrinkage': shrinkage,
    'color-continuity': color_continuity,
}
