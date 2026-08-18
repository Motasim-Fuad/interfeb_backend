from __future__ import annotations

import os
import re
import urllib.request
from io import BytesIO
from html import escape
from typing import Iterable

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, Image, Flowable, KeepTogether,
)
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.features.attachments.models import Attachment
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


INK = colors.black
PAPER = colors.white
HEADER_BG = colors.HexColor('#E8E8E8')
ROW_ALT = colors.HexColor('#F5F5F5')
LINE = colors.black
CELL_PAD = 3
IMAGE_PAD = 4
IMAGE_GRID_FUDGE = 1.4

styles = getSampleStyleSheet()
TITLE = ParagraphStyle('PaperTitle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=15, leading=18, alignment=TA_CENTER, textColor=INK, spaceAfter=2)
SUBTITLE = ParagraphStyle('PaperSubtitle', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, leading=13, alignment=TA_CENTER, textColor=INK)
LABEL = ParagraphStyle('PaperLabel', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=7.5, leading=9, textColor=INK)
CELL = ParagraphStyle('PaperCell', parent=styles['Normal'], fontName='Helvetica', fontSize=7.2, leading=8.8, alignment=TA_CENTER, textColor=INK)
SMALL = ParagraphStyle('PaperSmall', parent=styles['Normal'], fontName='Helvetica', fontSize=6.5, leading=7.8, alignment=TA_CENTER, textColor=INK)
CENTER = ParagraphStyle('PaperCenter', parent=CELL, alignment=TA_CENTER)
META_LABEL = ParagraphStyle('MetaLabel', parent=CELL, fontName='Helvetica-Bold', fontSize=7.2, alignment=TA_LEFT, textColor=INK)
META_VALUE = ParagraphStyle('MetaValue', parent=CELL, fontSize=7.6, alignment=TA_LEFT, textColor=INK)
PASS_STYLE = ParagraphStyle('PassCell', parent=CENTER, fontName='Helvetica-Bold', textColor=INK)
FAIL_STYLE = ParagraphStyle('FailCell', parent=CENTER, fontName='Helvetica-Bold', textColor=INK)
HEX_RE = re.compile(r'^#?([0-9A-Fa-f]{6})$')


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


def _unit(plm):
    raw = getattr(plm, 'measurement_unit', '') if plm else ''
    return (raw.value.upper() if hasattr(raw, 'value') else str(raw or '')).upper()


def _qty_unit(plm):
    return (getattr(plm, 'quantity_unit', '') or 'yds').upper() if plm else 'YDS'


def _plm(db: Session, plm_id):
    return db.query(Plm).options(
        joinedload(Plm.buyer),
        joinedload(Plm.supplier),
        joinedload(Plm.colors).joinedload(PlmColor.rolls),
    ).get(plm_id)


def _unique_colors(*groups) -> list[str]:
    seen = []
    for group in groups:
        if group is None:
            continue
        values = group if isinstance(group, (list, tuple, set)) else [group]
        for raw in values:
            for part in str(raw or '').split(','):
                text = part.strip()
                if text and text not in seen:
                    seen.append(text)
    return seen


def _plm_colors(plm: Plm, extras: Iterable[str] | None = None) -> list[str]:
    planned = [c.colour_code for c in (plm.colors or [])] if plm else []
    return _unique_colors(planned, extras)


def _planned_color_map(plm: Plm | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not plm:
        return mapping
    for color in (plm.colors or []):
        code = (color.colour_code or '').strip()
        for roll in (color.rolls or []):
            if roll.roll_no:
                mapping[str(roll.roll_no)] = code
    return mapping


def _color_for(plm: Plm | None, roll_no, stored=None, planned=None):
    text = str(stored or '').strip()
    if text:
        return text
    mapping = planned if planned is not None else _planned_color_map(plm)
    return mapping.get(str(roll_no or ''), '')


def _color_chip(code: str, width=28*mm):
    text = str(code or '').strip()
    if not text:
        return _p('')
    match = HEX_RE.match(text)
    label = _p(text if match else text, SMALL)
    if not match:
        return label
    hex_color = '#' + match.group(1)
    chip = Table([['']], colWidths=[5.2*mm], rowHeights=[5.2*mm])
    chip.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor(hex_color)),
        ('BOX', (0, 0), (-1, -1), 0.4, INK),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    inner = Table([[chip, label]], colWidths=[7*mm, max(12*mm, width - 7*mm)])
    inner.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 1),
        ('RIGHTPADDING', (0, 0), (-1, -1), 1),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return inner


def _color_header_value(codes: list[str]):
    if not codes:
        return _p('—', META_VALUE)
    return _p(', '.join(codes), META_VALUE)


def _status_cell(value):
    if value is True or str(value).upper() == 'PASS':
        return _p('PASS', PASS_STYLE)
    if value is False or str(value).upper() == 'FAIL':
        return _p('FAIL', FAIL_STYLE)
    if value in (None, '', 'PENDING'):
        return _p('PENDING' if value == 'PENDING' else '', CENTER)
    return _p(value, CENTER)


def _attachment_path(att: Attachment) -> str | None:
    candidates = []
    key = getattr(att, 'storage_key', None) or ''
    if key:
        candidates.append(os.path.join(settings.MEDIA_ROOT, key.lstrip('/')))
    url = (getattr(att, 'file_url', '') or '').replace('\\', '/')
    if url.startswith('/') and os.path.isfile(url):
        candidates.append(url)
    if '/media/' in url:
        media_key = url.split('/media/', 1)[-1].split('?', 1)[0]
        if media_key:
            candidates.append(os.path.join(settings.MEDIA_ROOT, media_key))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def _image_source(att: Attachment):
    path = _attachment_path(att)
    if path:
        return path
    url = getattr(att, 'file_url', '') or ''
    # Fetching this API's own localhost URL from inside the PDF request deadlocks the worker.
    if url.startswith('http://localhost') or url.startswith('http://127.0.0.1'):
        return None
    if url.startswith('http'):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Interfab-PDF/1.0'})
            with urllib.request.urlopen(req, timeout=8) as resp:
                return BytesIO(resp.read())
        except Exception:
            return None
    return None


def _find_attachment(db: Session, owner_type: str, owner_id):
    if owner_id is None:
        return None
    return (
        db.query(Attachment)
        .filter(Attachment.owner_type == owner_type, Attachment.owner_id == owner_id)
        .order_by(Attachment.created_at.desc())
        .first()
    )


class _ClippedPhoto(Flowable):
    """Fabric photo that never paints outside its box, so table grid lines stay visible."""

    def __init__(self, image: Image, width, height):
        super().__init__()
        self._image = image
        self._box_w = max(1.0, float(width))
        self._box_h = max(1.0, float(height))
        self._nat_w = max(1.0, float(getattr(image, 'drawWidth', width)))
        self._nat_h = max(1.0, float(getattr(image, 'drawHeight', height)))
        self._fit(self._box_w, self._box_h)

    def _fit(self, width, height):
        self._width = max(1.0, float(width))
        self._height = max(1.0, float(height))
        scale = min(self._width / self._nat_w, self._height / self._nat_h, 1.0)
        self._dw = self._nat_w * scale
        self._dh = self._nat_h * scale
        self._ox = (self._width - self._dw) / 2.0
        self._oy = (self._height - self._dh) / 2.0

    def wrap(self, availWidth, availHeight):
        # ReportLab measures a SPAN cell against the first row only during layout.
        w = self._box_w
        h = self._box_h
        if availWidth and availWidth > 0:
            w = min(w, float(availWidth))
        if availHeight and availHeight > 0:
            h = min(h, float(availHeight))
        self._fit(w, h)
        return self._width, self._height

    def draw(self):
        c = self.canv
        c.saveState()
        path = c.beginPath()
        path.rect(0, 0, self._width, self._height)
        c.clipPath(path, stroke=0, fill=0)
        self._image.drawWidth = self._dw
        self._image.drawHeight = self._dh
        self._image.wrap(self._dw, self._dh)
        self._image.drawOn(c, self._ox, self._oy)
        c.restoreState()


def _photo_inner(col_w, n_rows):
    inner_w = max(8.0, float(col_w) - 2 * IMAGE_PAD - IMAGE_GRID_FUDGE)
    span_h = max(int(n_rows), 1) * 8.4 * mm
    inner_h = min(18 * mm, max(9 * mm, span_h - 2 * IMAGE_PAD - IMAGE_GRID_FUDGE))
    return inner_w, inner_h


def _image_from_att(att: Attachment, max_w=28*mm, max_h=20*mm):
    source = _image_source(att)
    if source is None:
        return None
    try:
        if isinstance(source, BytesIO):
            payload = source.getvalue()
            reader = ImageReader(BytesIO(payload))
            img_input = BytesIO(payload)
        else:
            reader = ImageReader(source)
            img_input = source
        iw, ih = reader.getSize()
        if not iw or not ih:
            return None
        ratio = min(max_w / float(iw), max_h / float(ih), 1)
        img = Image(img_input, width=max(1.0, float(iw) * ratio), height=max(1.0, float(ih) * ratio))
        img.hAlign = 'CENTER'
        return img
    except Exception:
        return None


def _roll_image(db: Session, owner_type: str, owner_id, max_w=28*mm, max_h=20*mm):
    att = _find_attachment(db, owner_type, owner_id)
    if not att:
        return _p('')
    return _image_from_att(att, max_w, max_h) or _p('')


def _stage_image(db: Session, stage_type: str, report_id, *, fallback_types=(), fallback_roll_type=None, roll_ids=(), max_w=28*mm, max_h=20*mm):
    """One photo for a wash-stage / report table — latest stage attachment, else first legacy roll photo."""
    for owner_type in (stage_type, *fallback_types):
        att = _find_attachment(db, owner_type, report_id)
        if att:
            img = _image_from_att(att, max_w, max_h)
            if img is not None:
                return img
    if fallback_roll_type:
        for rid in roll_ids:
            att = _find_attachment(db, fallback_roll_type, rid)
            if att:
                img = _image_from_att(att, max_w, max_h)
                if img is not None:
                    return img
    return _p('')


def _image_col_spans(data_row_count: int, last_col: int):
    if data_row_count <= 1:
        return []
    return [
        ('SPAN', (last_col, 1), (last_col, data_row_count)),
        ('VALIGN', (last_col, 1), (last_col, data_row_count), 'MIDDLE'),
        ('ALIGN', (last_col, 1), (last_col, data_row_count), 'CENTER'),
    ]


def _stage_photo_table(data, widths, *, image_col, n_data_rows, extra_rows=0, header_rows=1, font=7.1):
    """Last-column stage photo: sized to the column, clipped, rowspan aligned to row heights."""
    extra_cmds = []
    row_heights = None
    if n_data_rows > 0 and header_rows < len(data) and image_col < len(data[header_rows]):
        raw = data[header_rows][image_col]
        if isinstance(raw, (Image, _ClippedPhoto)):
            inner_w, inner_h = _photo_inner(widths[image_col], n_data_rows)
            photo = raw if isinstance(raw, _ClippedPhoto) else _ClippedPhoto(raw, inner_w, inner_h)
            data[header_rows][image_col] = photo
            last_data = header_rows + n_data_rows - 1
            extra_cmds = [
                ('LEFTPADDING', (image_col, header_rows), (image_col, last_data), IMAGE_PAD),
                ('RIGHTPADDING', (image_col, header_rows), (image_col, last_data), IMAGE_PAD),
                ('TOPPADDING', (image_col, header_rows), (image_col, last_data), IMAGE_PAD),
                ('BOTTOMPADDING', (image_col, header_rows), (image_col, last_data), IMAGE_PAD),
                ('VALIGN', (image_col, header_rows), (image_col, last_data), 'MIDDLE'),
                ('ALIGN', (image_col, header_rows), (image_col, last_data), 'CENTER'),
            ]
            need = photo._height + 2 * IMAGE_PAD + IMAGE_GRID_FUDGE
            per = max(8.4 * mm, need / n_data_rows)
            row_heights = [9 * mm] * header_rows + [per] * n_data_rows + [9 * mm] * extra_rows
    return _table(
        data,
        widths=widths,
        header_rows=header_rows,
        font=font,
        spans=_image_col_spans(n_data_rows, image_col),
        row_heights=row_heights,
        extra_cmds=extra_cmds,
    )


def _cell(value, font=7.0):
    if isinstance(value, Flowable):
        return value
    if value in ('PASS', 'FAIL', 'PENDING') or value is True or value is False:
        return _status_cell(value)
    style = ParagraphStyle(f'c{font}', parent=CENTER, fontSize=font, leading=font + 1.4)
    return _p(value, style)


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


def _table(data, widths=None, header_rows=1, font=7.0, spans=None, row_colors=None, row_heights=None, extra_cmds=None):
    wrapped = []
    for ri, row in enumerate(data):
        header_style = ParagraphStyle(
            f'h{font}_{ri}', parent=CENTER, fontName='Helvetica-Bold', fontSize=font, leading=font + 1.4, textColor=INK,
        )
        cells = []
        for ci, x in enumerate(row):
            if isinstance(x, Flowable) or x in ('PASS', 'FAIL', 'PENDING') or isinstance(x, bool):
                cells.append(_cell(x, font))
            elif ri < header_rows:
                cells.append(_p(x, header_style))
            elif header_rows == 0 and ci % 2 == 0:
                cells.append(_p(x, ParagraphStyle(f'kv{font}', parent=META_LABEL, fontSize=font, leading=font + 1.4)))
            else:
                cells.append(_cell(x, font))
        wrapped.append(cells)
    t = Table(wrapped, colWidths=widths, rowHeights=row_heights, repeatRows=max(header_rows, 0), hAlign='LEFT')
    cmds = [
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('LEFTPADDING', (0, 0), (-1, -1), CELL_PAD),
        ('RIGHTPADDING', (0, 0), (-1, -1), CELL_PAD),
        ('TOPPADDING', (0, 0), (-1, -1), CELL_PAD),
        ('BOTTOMPADDING', (0, 0), (-1, -1), CELL_PAD),
        ('TEXTCOLOR', (0, 0), (-1, -1), INK),
        ('BACKGROUND', (0, 0), (-1, -1), PAPER),
    ]
    if header_rows:
        cmds += [
            ('BACKGROUND', (0, 0), (-1, header_rows - 1), HEADER_BG),
            ('FONTNAME', (0, 0), (-1, header_rows - 1), 'Helvetica-Bold'),
        ]
        for i in range(header_rows, len(data)):
            if (i - header_rows) % 2 == 1:
                cmds.append(('BACKGROUND', (0, i), (-1, i), ROW_ALT))
    else:
        for row_i, row in enumerate(data):
            for col_i in range(0, len(row), 2):
                cmds.append(('BACKGROUND', (col_i, row_i), (col_i, row_i), HEADER_BG))
                cmds.append(('FONTNAME', (col_i, row_i), (col_i, row_i), 'Helvetica-Bold'))
                cmds.append(('ALIGN', (col_i, row_i), (col_i, row_i), 'LEFT'))
                cmds.append(('ALIGN', (col_i + 1, row_i), (col_i + 1, row_i), 'LEFT'))
    if row_colors:
        cmds.extend(row_colors)
    if extra_cmds:
        cmds.extend(extra_cmds)
    if spans:
        cmds.extend(spans)
    # Draw grid last so column borders sit on top of any photo pixels.
    cmds.append(('GRID', (0, 0), (-1, -1), 0.45, LINE))
    t.setStyle(TableStyle(cmds))
    return t


def _banner(title: str, page_width):
    banner = Table(
        [[_p('Interfab Shirt Manufacturing Ltd.', TITLE)], [_p(title, SUBTITLE)]],
        colWidths=[page_width],
    )
    banner.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('BACKGROUND', (0, 0), (-1, -1), PAPER),
        ('TEXTCOLOR', (0, 0), (-1, -1), INK),
        ('TOPPADDING', (0, 0), (-1, 0), 2),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 1),
        ('TOPPADDING', (0, 1), (-1, 1), 1),
        ('BOTTOMPADDING', (0, 1), (-1, 1), 5),
        ('LINEBELOW', (0, 1), (-1, 1), 1.1, INK),
    ]))
    return banner


def _header_story(title: str, plm: Plm, *, color_codes=None, extra_meta=None, landscape_page=False):
    page_width = (landscape(A4)[0] if landscape_page else A4[0]) - 16*mm
    buyer = plm.buyer.name if plm and plm.buyer else ''
    supplier = plm.supplier.name if plm and plm.supplier else ''
    style_plm = ' / '.join(x for x in [plm.plm_code if plm else '', plm.plm_style if plm else ''] if x)
    codes = list(color_codes or [])
    meta = Table([
        [_p('Buyer', META_LABEL), _p(buyer, META_VALUE), _p('Supplier', META_LABEL), _p(supplier, META_VALUE)],
        [_p('PLM / Style', META_LABEL), _p(style_plm, META_VALUE), _p('Color', META_LABEL), _color_header_value(codes)],
        [_p('Consignment', META_LABEL), _p(getattr(plm, 'consignment', '') or '', META_VALUE), _p('Unit', META_LABEL), _p(_unit(plm), META_VALUE)],
    ], colWidths=[24*mm, page_width/2 - 24*mm, 24*mm, page_width/2 - 24*mm])
    meta.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), HEADER_BG),
        ('BACKGROUND', (2, 0), (2, -1), HEADER_BG),
        ('BACKGROUND', (1, 0), (1, -1), PAPER),
        ('BACKGROUND', (3, 0), (3, -1), PAPER),
        ('BOX', (0, 0), (-1, -1), 0.45, INK),
        ('INNERGRID', (0, 0), (-1, -1), 0.4, INK),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TEXTCOLOR', (0, 0), (-1, -1), INK),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story = [_banner(title, page_width), Spacer(1, 3*mm), meta]
    if extra_meta:
        story += [Spacer(1, 2.5*mm), extra_meta]
    story.append(Spacer(1, 3.5*mm))
    return story


def _section_title(text, landscape_page=False):
    page_width = (landscape(A4)[0] if landscape_page else A4[0]) - 16*mm
    bar = Table([[_p(text, ParagraphStyle('sec', parent=LABEL, fontSize=8.5))]], colWidths=[page_width])
    bar.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), PAPER),
        ('TEXTCOLOR', (0, 0), (-1, -1), INK),
        ('LINEBELOW', (0, 0), (-1, -1), 0.8, INK),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    return bar


def _fabric_inspection(db, plm):
    report = (
        db.query(fi_models.FabricInspectionReport)
        .options(joinedload(fi_models.FabricInspectionReport.rolls).joinedload(fi_models.FabricInspectionRoll.defects))
        .filter(fi_models.FabricInspectionReport.plm_id == plm.id).first()
    )
    if not report:
        return None
    stats = compute_fi_stats(report)
    planned = _planned_color_map(plm)
    all_defect_names = []
    for r in report.rolls:
        for d in r.defects:
            n = d.defect_type_name or ''
            if n and n not in all_defect_names:
                all_defect_names.append(n)
    defect_names = all_defect_names[:8]
    extra_defect_names = all_defect_names[8:]
    rows = [['Roll No', 'Color', 'Req W', 'Act W', 'Marked L', 'Actual L', *defect_names, 'Total', '% Points', 'Pts/Yds', 'Remarks']]
    for r in report.rolls:
        by = {n: 0 for n in defect_names}
        for d in r.defects:
            n = d.defect_type_name or ''
            if n in by:
                by[n] += int(d.points_snapshot or 0)
        pts_yds = (float(r.total_points or 0) / float(r.length_actual)) if r.length_actual not in (None, 0) else None
        rows.append([
            r.roll_no, _color_chip(_color_for(plm, r.roll_no, r.colour_code, planned)), _fmt(r.width_marked), _fmt(r.width_actual),
            _fmt(r.length_marked), _fmt(r.length_actual), *[str(by[n]) if by[n] else '' for n in defect_names],
            r.total_points, _fmt(r.percent_points), _fmt(pts_yds), r.remarks or '',
        ])
    colors_text = _plm_colors(plm, [r.colour_code for r in report.rolls] + [report.color] + list(planned.values()))
    info = _table([
        ['Received Qty', f'{_fmt(report.received_qty)} {report.quantity_unit_snapshot.upper()}', 'Inspection Check Qty', f'{_fmt(report.checked_qty)} {report.quantity_unit_snapshot.upper()}'],
        ['Fabric Type', report.fabric_type or '', 'Fabric Composition', report.fabric_composition or ''],
        ['Individual Accepted', f'{_fmt(report.individual_standard_snapshot)}%', 'Average Accepted', f'{_fmt(report.average_standard_snapshot)}%'],
        ['In-house Date', _date(report.fabric_in_house_date), 'Inspection Date', _date(report.inspection_date)],
    ], widths=[32*mm, 68*mm, 38*mm, 62*mm], header_rows=0, font=7.5)
    story = _header_story('FABRICS INSPECTION REPORT — 4 POINTS SYSTEM', plm, color_codes=colors_text, extra_meta=info, landscape_page=True)
    pagew = landscape(A4)[0] - 16*mm
    fixed = [16, 22, 14, 14, 16, 16]
    tail_width = 64*mm
    remaining = pagew - sum(x*mm for x in fixed) - tail_width
    dyn = (remaining / max(1, len(defect_names))) if defect_names else 0
    widths = [16*mm, 22*mm, 14*mm, 14*mm, 16*mm, 16*mm] + [dyn] * len(defect_names) + [13*mm, 15*mm, 14*mm, 22*mm]
    story += [_table(rows, widths=widths, header_rows=1, font=5.7), Spacer(1, 3*mm)]
    if extra_defect_names:
        extra = [['Roll No', *extra_defect_names]]
        for r in report.rolls:
            by = {n: 0 for n in extra_defect_names}
            for d in r.defects:
                n = d.defect_type_name or ''
                if n in by:
                    by[n] += int(d.points_snapshot or 0)
            extra.append([r.roll_no, *[str(by[n]) if by[n] else '' for n in extra_defect_names]])
        extra_width = (pagew - 18*mm) / max(1, len(extra_defect_names))
        story += [_p('Additional Defect Breakdown', LABEL), _table(extra, widths=[18*mm] + [extra_width] * len(extra_defect_names), header_rows=1, font=5.4), Spacer(1, 3*mm)]
    result = 'PASS' if stats['is_pass'] else 'FAIL' if stats['is_pass'] is False else ''
    story += [_table([['Total Defect Points', stats['total_defect_points'], 'Overall % Points', _fmt(stats['overall_percent_points']), 'Result', result]], widths=[36*mm, 28*mm, 36*mm, 28*mm, 24*mm, 28*mm], header_rows=0, font=7.5)]
    return story, True


def _gsm(db, plm):
    r = db.query(gsm_models.GsmCheckReport).options(joinedload(gsm_models.GsmCheckReport.rolls)).filter(gsm_models.GsmCheckReport.plm_id == plm.id).first()
    if not r:
        return None
    stats = gsm_service.compute_report_stats(r.rolls, float(r.standard_gsm or 0), float(r.tolerance_snapshot) if r.tolerance_snapshot is not None else None)
    planned = _planned_color_map(plm)
    codes = _plm_colors(plm, [x.color for x in r.rolls] + [r.color] + list(planned.values()))
    report_date = _date(r.report_date) or next((_date(x.before_wash_date or x.after_wash_date) for x in r.rolls if x.before_wash_date or x.after_wash_date), '')
    info = _table([['Required GSM', _fmt(r.standard_gsm), 'Tolerance', f'±{_fmt(r.tolerance_snapshot)}%', 'Date', report_date]], widths=[30*mm, 24*mm, 24*mm, 24*mm, 20*mm, 32*mm], header_rows=0, font=8)
    story = _header_story('GSM Check Report', plm, color_codes=codes, extra_meta=info)

    def section(title, after=False):
        data = [['Roll No', 'Color', 'Require GSM', 'Actual GSM', 'Over / Less %', 'Remarks', 'Fabric Image']]
        rows = []
        for x in r.rolls:
            actual = x.actual_gsm_after if after else x.actual_gsm_before
            if actual is None:
                continue
            rows.append(x)
        img = _stage_image(
            db, 'gsm_after_wash' if after else 'gsm_before_wash', r.id,
            fallback_roll_type='gsm_roll' if not after else None,
            roll_ids=[x.id for x in rows],
        )
        for i, x in enumerate(rows):
            actual = x.actual_gsm_after if after else x.actual_gsm_before
            pct = gsm_service.over_less_pct(float(actual), float(r.standard_gsm or 0))
            rem = x.remarks_after if after else x.remarks_before
            data.append([x.roll_no, _color_chip(_color_for(plm, x.roll_no, x.color, planned)), _fmt(r.standard_gsm), _fmt(actual), _fmt(pct), rem or '', img if i == 0 else ''])
        if rows:
            avg = stats['avg_over_less_after_pct' if after else 'avg_over_less_before_pct']
            passed = stats['pass_after' if after else 'pass_before']
            data.append(['AVG', '', '', '', f'{_fmt(avg)}%', 'PASS' if passed is True else 'FAIL' if passed is False else '', ''])
        else:
            data.append(['—', '', _fmt(r.standard_gsm), '', '', 'No data', ''])
        return KeepTogether([_section_title(title), Spacer(1, 1.5*mm), _stage_photo_table(
            data, [18*mm, 28*mm, 24*mm, 24*mm, 26*mm, 30*mm, 24*mm],
            image_col=6, n_data_rows=len(rows), extra_rows=1, font=7.1,
        )])

    story += [section('BEFORE WASH', False), Spacer(1, 4*mm), section('AFTER WASH', True)]
    return story, False


def _moisture(db, plm):
    r = db.query(moisture_models.MoistureCheckReport).options(joinedload(moisture_models.MoistureCheckReport.rolls)).filter(moisture_models.MoistureCheckReport.plm_id == plm.id).first()
    if not r:
        return None
    moisture_cfg = (plm.qc_config or {}).get('moisture', {}) if isinstance(plm.qc_config, dict) else {}
    standard_label = moisture_cfg.get('standard_label') or plm.fabric_composition or 'Selected composition'
    planned = _planned_color_map(plm)
    codes = _plm_colors(plm, [x.color for x in r.rolls] + list(planned.values()))
    info = _table([
        ['Composition Standard', standard_label, 'Acceptance', f'{_fmt(r.acceptance_snapshot)}%'],
        ['Vendor', r.vendor or '', 'Factory', r.factory or 'Interfab Shirt Mfg. Ltd.'],
    ], widths=[34*mm, 63*mm, 28*mm, 69*mm], header_rows=0, font=7.5)
    story = _header_story('Moisture Check List', plm, color_codes=codes, extra_meta=info)

    def section(title, after=False):
        data = [['Roll No', 'Fabric Composition', 'Color', 'Rating', 'Pass/Fail', 'Date', 'Fabric Image']]
        rows = []
        for x in r.rolls:
            val = x.swatch_rating_after if after else x.swatch_rating_before
            if val is None:
                continue
            rows.append(x)
        img = _stage_image(
            db, 'moisture_after_wash' if after else 'moisture_before_wash', r.id,
        )
        for i, x in enumerate(rows):
            val = x.swatch_rating_after if after else x.swatch_rating_before
            d = x.after_wash_date if after else x.before_wash_date
            passed = float(val) <= float(r.acceptance_snapshot) if r.acceptance_snapshot is not None else None
            data.append([x.roll_no, x.fabric_composition or plm.fabric_composition or '', _color_chip(_color_for(plm, x.roll_no, x.color, planned)), f'{_fmt(val)}%', 'PASS' if passed else 'FAIL' if passed is False else '', _date(d), img if i == 0 else ''])
        if not rows:
            data.append(['—', '', '', '', 'No data', '', ''])
        return KeepTogether([_section_title(title), Spacer(1, 1.5*mm), _stage_photo_table(
            data, [18*mm, 36*mm, 26*mm, 20*mm, 22*mm, 24*mm, 24*mm],
            image_col=6, n_data_rows=len(rows), font=7.1,
        )])

    story += [section('BEFORE WASH', False), Spacer(1, 4*mm), section('AFTER WASH', True)]
    return story, False


def _skew(db, plm):
    r = db.query(skew_models.SkewBowingReport).options(joinedload(skew_models.SkewBowingReport.rolls)).filter(skew_models.SkewBowingReport.plm_id == plm.id).first()
    if not r:
        return None
    std = float(r.standard_point_snapshot) if r.standard_point_snapshot is not None else None
    stats = skew_service.compute_report_stats(r.rolls, std, r.measurement_unit_snapshot)
    planned = _planned_color_map(plm)
    codes = _plm_colors(plm, [x.colour_code for x in r.rolls] + list(planned.values()))
    info = _table([
        ['Receive Qty', f'{_fmt(r.receive_qty)} {r.quantity_unit_snapshot.upper()}', 'Total Roll', r.total_roll or '', 'Fabric Construction', r.fabric_construction or ''],
        ['In-house Date', _date(r.in_house_date), 'Fabric Width Standard', r.fabric_width_label_snapshot or '', 'Fabric Type', 'STRIPE' if r.is_stripe else 'SOLID'],
        ['Acceptance', f'{_fmt(std)} {r.measurement_unit_snapshot.upper()}', '', '', '', ''],
    ], widths=[28*mm, 36*mm, 25*mm, 25*mm, 35*mm, 45*mm], header_rows=0, font=7.5)
    extra = info
    story = _header_story('Skew & Bowing Check Report', plm, color_codes=codes, extra_meta=extra)

    def section(title, after=False):
        data = [['Roll No', 'Color', 'AC', 'BD', '|AC-BD|', 'AC+BD', 'Skew %', 'Result', 'Fabric Image']]
        rows = []
        for x in r.rolls:
            ac = x.ac_value_after if after else x.ac_value_before
            bd = x.bd_value_after if after else x.bd_value_before
            if ac is None and bd is None:
                continue
            rows.append(x)
        img = _stage_image(
            db, 'skew_after_wash' if after else 'skew_before_wash', r.id,
            fallback_types=('skew_bowing_report',) if not after else (),
            fallback_roll_type='skew_bowing_roll' if not after else None,
            roll_ids=[x.id for x in rows],
        )
        for i, x in enumerate(rows):
            ac = x.ac_value_after if after else x.ac_value_before
            bd = x.bd_value_after if after else x.bd_value_before
            pct = skew_service.skew_percent(ac, bd, None, r.measurement_unit_snapshot)
            amount = abs(float(ac) - float(bd)) if ac is not None and bd is not None else None
            total = float(ac) + float(bd) if ac is not None and bd is not None else None
            passed = amount <= std if amount is not None and std is not None else None
            data.append([x.roll_no, _color_chip(_color_for(plm, x.roll_no, x.colour_code, planned)), _fmt(ac), _fmt(bd), _fmt(amount), _fmt(total), f'{_fmt(pct)}%', 'PASS' if passed else 'FAIL' if passed is False else '', img if i == 0 else ''])
        if rows:
            avg = stats['avg_percent_after' if after else 'avg_percent_before']
            data.append(['AVG', '', '', '', '', '', f'{_fmt(avg)}%' if avg is not None else '', '', ''])
        else:
            data.append(['—', '', '', '', '', '', '', 'No data', ''])
        return KeepTogether([_section_title(title), Spacer(1, 1.5*mm), _stage_photo_table(
            data, [16*mm, 26*mm, 16*mm, 16*mm, 18*mm, 18*mm, 20*mm, 20*mm, 24*mm],
            image_col=8, n_data_rows=len(rows), extra_rows=1, font=6.8,
        )])

    story += [section('BEFORE WASH', False), Spacer(1, 4*mm), section('AFTER WASH', True)]
    return story, False


def _relaxation(db, plm):
    r = db.query(relax_models.FabricRelaxationReport).options(joinedload(relax_models.FabricRelaxationReport.rolls)).filter(relax_models.FabricRelaxationReport.plm_id == plm.id).first()
    if not r:
        return None
    planned = _planned_color_map(plm)
    codes = _plm_colors(plm, [x.color_code for x in r.rolls] + [r.color] + list(planned.values()))
    story = _header_story('FABRIC RELAXATION TEST REPORT', plm, color_codes=codes, landscape_page=True)
    data = [['Color', 'Roll No', f'Fabric {_qty_unit(plm)}', 'Start Time', 'Before L', 'Before W', 'After Time', 'After L', 'After W', 'Change L %', 'Change W %', 'Remark', 'Fabric Image']]
    img = _stage_image(db, 'relaxation_report', r.id, fallback_roll_type='relaxation_roll', roll_ids=[x.id for x in r.rolls])
    for i, x in enumerate(r.rolls):
        f = relax_service.compute_roll_fields(x)
        data.append([
            _color_chip(_color_for(plm, x.roll_no, x.color_code, planned)), x.roll_no, _fmt(x.fabric_yds_mtr), x.before_relax_start_time or '',
            _fmt(x.before_relax_length), _fmt(x.before_relax_width), x.after_relax_time or '',
            _fmt(x.after_relax_length), _fmt(x.after_relax_width), _fmt(f['shrinkage_length_pct']),
            _fmt(f['shrinkage_width_pct']), x.remark or '', img if i == 0 else '',
        ])
    story += [_stage_photo_table(
        data, [22*mm, 16*mm, 18*mm, 22*mm, 16*mm, 16*mm, 22*mm, 16*mm, 16*mm, 20*mm, 20*mm, 26*mm, 24*mm],
        image_col=12, n_data_rows=len(r.rolls), font=6.2,
    ), Spacer(1, 3*mm)]
    story += [_p(f'Relaxation time (Dashboard): {r.relaxation_hours_snapshot or 12} HOURS', LABEL)]
    return story, True


def _shrinkage(db, plm):
    r = (
        db.query(shrink_models.ShrinkagePatternReport)
        .options(joinedload(shrink_models.ShrinkagePatternReport.rolls).joinedload(shrink_models.ShrinkageRoll.measurements))
        .filter(shrink_models.ShrinkagePatternReport.plm_id == plm.id).first()
    )
    if not r:
        return None
    planned = _planned_color_map(plm)
    codes = _plm_colors(plm, [r.color] + list(planned.values()))
    story = _header_story('Shrinkage & Pattern Adjustment Report', plm, color_codes=codes, landscape_page=True)
    data = [['Roll', 'Qty', 'Reading', 'Before L', 'Before W', 'After L', 'After W', 'L %', 'W %', 'Avg L %', 'Avg W %', 'Pattern', 'Shade']]
    spans = []
    for x in r.rolls:
        measurements = sorted(x.measurements, key=lambda z: z.reading_order) or [None]
        avgs = shrink_service.compute_roll_fields(x, None)
        start = len(data)
        for i, m in enumerate(measurements):
            if m is None:
                data.append([x.roll_no, _fmt(x.yds), '', '', '', '', '', '', '', _fmt(avgs['avg_length_pct']), _fmt(avgs['avg_width_pct']), x.pattern or '', x.shade or ''])
                continue
            f = shrink_service.compute_measurement_fields(m)
            data.append([
                x.roll_no if i == 0 else '',
                _fmt(x.yds) if i == 0 else '',
                m.reading_order,
                _fmt(m.before_length), _fmt(m.before_width), _fmt(m.after_length), _fmt(m.after_width),
                _fmt(f['adjust_length_pct']), _fmt(f['adjust_width_pct']),
                _fmt(avgs['avg_length_pct']) if i == 0 else '',
                _fmt(avgs['avg_width_pct']) if i == 0 else '',
                x.pattern or '' if i == 0 else '',
                x.shade or '' if i == 0 else '',
            ])
        end = len(data) - 1
        if end > start:
            for col in (0, 1, 9, 10, 11, 12):
                spans.append(('SPAN', (col, start), (col, end)))
                spans.append(('VALIGN', (col, start), (col, end), 'MIDDLE'))
    story += [_table(data, widths=[18*mm, 16*mm, 16*mm, 20*mm, 20*mm, 20*mm, 20*mm, 18*mm, 18*mm, 20*mm, 20*mm, 20*mm, 20*mm], header_rows=1, font=6.5, spans=spans)]
    return story, True


def _shade(db, plm):
    r = (
        db.query(shade_models.ShadeCheckReport)
        .options(joinedload(shade_models.ShadeCheckReport.rolls).joinedload(shade_models.ShadeCheckRoll.results))
        .filter(shade_models.ShadeCheckReport.plm_id == plm.id).first()
    )
    if not r:
        return None
    points = sorted((r.points_snapshot or []), key=lambda x: int(x.get('sort_order', 0)))
    length = [p for p in points if p.get('check_type') == 'length']
    width = [p for p in points if p.get('check_type') == 'width']
    planned = _planned_color_map(plm)
    codes = _plm_colors(plm, [roll.colour for roll in r.rolls] + list(planned.values()))
    story = _header_story('Fabric Shade Check Report', plm, color_codes=codes, landscape_page=True)
    headers = ['Date', 'Colour', 'Cons', 'Roll No'] + [p.get('label', p.get('check_key', '')) for p in length]
    for p in width:
        headers += [f"{p.get('label', '')} FIRST", f"{p.get('label', '')} MIDDLE", f"{p.get('label', '')} LAST"]
    headers += ['Remarks', 'Fabric Image']
    data = [headers]
    img = _stage_image(db, 'shade_check_report', r.id, fallback_roll_type='shade_check_roll', roll_ids=[roll.id for roll in r.rolls])
    for i, roll in enumerate(r.rolls):
        by = {x.point_key: x for x in roll.results}
        row = [_date(roll.roll_date), _color_chip(_color_for(plm, roll.roll_no, roll.colour, planned)), _fmt(roll.cons), roll.roll_no]
        for p in length:
            x = by.get(p.get('check_key'))
            row.append('OK' if x and x.first_ok is True else 'NG' if x and x.first_ok is False else '')
        for p in width:
            x = by.get(p.get('check_key'))
            for v in [getattr(x, 'first_ok', None) if x else None, getattr(x, 'middle_ok', None) if x else None, getattr(x, 'last_ok', None) if x else None]:
                row.append('OK' if v is True else 'NG' if v is False else '')
        row.append(roll.remarks or '')
        row.append(img if i == 0 else '')
        data.append(row)
    available = landscape(A4)[0] - 16*mm
    widths = [18*mm, 24*mm, 14*mm, 16*mm]
    remaining_cols = len(headers) - 6
    rem = available - sum(widths) - 42*mm
    each = rem / max(1, remaining_cols)
    widths += [each] * remaining_cols + [22*mm, 24*mm]
    story += [_stage_photo_table(data, widths, image_col=len(headers) - 1, n_data_rows=len(r.rolls), font=5.2)]
    return story, True


def _continuity(db, plm):
    r = db.query(cc_models.ColorContinuityCard).options(joinedload(cc_models.ColorContinuityCard.rolls)).filter(cc_models.ColorContinuityCard.plm_id == plm.id).first()
    if not r:
        return None
    codes = _plm_colors(plm, list(_planned_color_map(plm).values()))
    story = _header_story('Master Color Continuity Card', plm, color_codes=codes)
    data = [['Roll No', 'Before Wash / Non Wash Shade', 'After Wash Shade', f'Qty ({_qty_unit(plm)})']]
    for x in r.rolls:
        data.append([x.roll_no, x.shade_group_before or '', x.shade_group_after or '', _fmt(x.yardage)])
    story += [_table(data, widths=[32*mm, 62*mm, 62*mm, 38*mm], header_rows=1, font=8)]
    return story, False


def _correlation(db, plm):
    report = corr_routes._ensure_report(db, plm)
    corr_routes._sync_from_sources(db, report, plm)
    db.flush()
    report = (db.query(corr_models.FabricCorrelationReport).options(joinedload(corr_models.FabricCorrelationReport.rolls).joinedload(corr_models.FabricCorrelationRoll.defects)).get(report.id))
    for row in report.rolls:
        for k, v in corr_service.compute_roll_fields(row, report.measurement_unit_snapshot, report.quantity_unit_snapshot).items():
            setattr(row, k, v)

    inner_widths = [12*mm, 25*mm, 14*mm, 14*mm, 15*mm, 17*mm, 13*mm, 8*mm, 14*mm]
    story = []
    rolls = list(report.rolls or [])
    chunks = [rolls[i:i + 9] for i in range(0, len(rolls), 9)] or [[]]
    codes = _plm_colors(plm, list(_planned_color_map(plm).values()))
    for ci, chunk in enumerate(chunks):
        if ci:
            story.append(PageBreak())
        story += _header_story('Fabric Correlation Report', plm, color_codes=codes, landscape_page=True)
        mill = [['Roll No', 'Defect Point', f'Length ({report.quantity_unit_snapshot.upper()})', f'Width ({report.measurement_unit_snapshot.upper()})', 'Total Point', 'Average Point', 'Standard', 'SCS', 'Pass/Fail']]
        factory = [['Roll No', 'Defects', f'Length ({report.quantity_unit_snapshot.upper()})', f'Width ({report.measurement_unit_snapshot.upper()})', 'Total Point', 'Average Point', 'Standard', 'SCS', 'Pass/Fail']]
        for x in chunk:
            active = []
            for d in x.defects:
                if int(d.count or 0) <= 0:
                    continue
                name = d.defect_type_name_snapshot or 'Defect'
                label = d.range_label_snapshot or ''
                active.append(f'{name} {label}: {int(d.count)} / {int(d.points_snapshot or 0)}pt')
            mill.append([
                x.roll_no, 'Supplier Total', _fmt(x.length_yards), _fmt(x.width), _fmt(x.mill_total_point),
                _fmt(getattr(x, 'mill_average_point', None)), _fmt(x.standard), x.scs,
                'PASS' if getattr(x, 'mill_pass', None) is True else 'FAIL' if getattr(x, 'mill_pass', None) is False else 'PENDING',
            ])
            factory.append([
                x.roll_no, '; '.join(active) if active else 'No defect', _fmt(x.factory_length), _fmt(x.factory_width), _fmt(x.factory_total_points),
                _fmt(getattr(x, 'factory_average_point', None)), _fmt(x.standard), x.scs,
                'PASS' if getattr(x, 'factory_pass', None) is True else 'FAIL' if getattr(x, 'factory_pass', None) is False else 'PENDING',
            ])
        left = [_section_title('Mill Report'), Spacer(1, 1.5*mm), _table(mill, widths=inner_widths, header_rows=1, font=4.4)]
        right = [_section_title('Factory Report'), Spacer(1, 1.5*mm), _table(factory, widths=inner_widths, header_rows=1, font=4.4)]
        holder = Table([[left, right]], colWidths=[136*mm, 136*mm], hAlign='LEFT')
        holder.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        story.append(holder)
    return story, True


def _range_text(values):
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return ''
    lo = min(vals)
    hi = max(vals)
    return _fmt(lo) if abs(lo - hi) < 1e-9 else f'{_fmt(lo)} - {_fmt(hi)}'


def _four_point_for_export(db, plm_id):
    return (
        db.query(fi_models.FabricInspectionReport)
        .options(joinedload(fi_models.FabricInspectionReport.rolls))
        .filter(fi_models.FabricInspectionReport.plm_id == plm_id).first()
    )


def _factory(db, plm):
    report = factory_routes._ensure_derived(db, plm)
    if not report:
        return None
    planned = (
        db.query(PlmRoll, PlmColor)
        .join(PlmColor, PlmRoll.plm_color_id == PlmColor.id)
        .filter(PlmColor.plm_id == plm.id)
        .order_by(PlmColor.colour_code, PlmRoll.roll_no).all()
    )
    mill_points = sum(float(r.supplier_total_point or 0) for r, c in planned)
    mill_rolls = [r for r, c in planned if r.marked_length is not None and r.marked_width is not None]
    mill_area = 0.0
    mill_length = 0.0
    mill_widths = []
    for r in mill_rolls:
        length = float(r.marked_length or 0)
        width = float(r.marked_width or 0)
        if length > 0 and width > 0:
            mill_length += length
            mill_widths.append(width)
            try:
                from app.features.fabric_inspection.service import width_to_inches, length_to_yards
                mill_area += width_to_inches(width, plm.measurement_unit.value if hasattr(plm.measurement_unit, 'value') else str(plm.measurement_unit)) * length_to_yards(length, plm.quantity_unit)
            except ValueError:
                pass
    mill_avg = (mill_points * 3600 / mill_area) if mill_area > 0 else None
    standard = float(report.standard_snapshot) if report.standard_snapshot is not None else None
    mill_pass = (mill_avg <= standard) if mill_avg is not None and standard is not None else None
    source = _four_point_for_export(db, plm.id)
    factory_widths = [float(r.width_actual) for r in (source.rolls if source else []) if r.width_actual is not None and float(r.width_actual) > 0]
    mill_width_text = _range_text(mill_widths)
    factory_width_text = _range_text(factory_widths)
    codes = _plm_colors(plm, [report.color] + [c.colour_code for r, c in planned])
    story = _header_story('Factory Report', plm, color_codes=codes, landscape_page=True)

    def summary_panel(title, *, yardage, pieces, width_text, total_point, avg_point, percentage, stage, status):
        info = _table([
            ['Customer', plm.buyer.name if plm.buyer else '', 'Job No.', report.job_no or plm.plm_code],
            ['Construction', report.construction or plm.fabric_composition or '', 'Color', report.color or (', '.join(codes) if codes else '')],
            ['Order Qty', f'{_fmt(report.order_qty)} {_qty_unit(plm)}', 'Inspection Date', _date(report.inspection_date)],
        ], widths=[22*mm, 44*mm, 20*mm, 42*mm], header_rows=0, font=6.1)
        status_table = _table([
            ['Inspection Yardage', f'{_fmt(yardage)} {_qty_unit(plm)}', 'Inspection Pieces', pieces],
            ['Actual Width', width_text, 'Inspection %', f'{_fmt(percentage)}%' if percentage is not None else ''],
            ['Inspection Stage', stage, 'Total Point', _fmt(total_point)],
        ], widths=[28*mm, 34*mm, 28*mm, 38*mm], header_rows=0, font=5.9)
        result = _table([
            ['Average Shipment Point', 'Actual', 'Standard', 'Pass/Fail'],
            ['', _fmt(avg_point), _fmt(standard), 'PASS' if status is True else 'FAIL' if status is False else 'PENDING'],
        ], widths=[44*mm, 27*mm, 27*mm, 30*mm], header_rows=1, font=6.2)
        return [_section_title(title), Spacer(1, 1.5*mm), info, Spacer(1, 2*mm), _p('Inspection Status', LABEL), status_table, Spacer(1, 2*mm), _p('Result', LABEL), result]

    mill_pct = 100.0 if mill_length > 0 else None
    left = summary_panel('Supplier / Mill Summary', yardage=mill_length or plm.allocated_quantity, pieces=len(planned), width_text=mill_width_text, total_point=mill_points, avg_point=mill_avg, percentage=mill_pct, stage='Supplier Report', status=mill_pass)
    right = summary_panel('Factory Inspection', yardage=report.inspection_yardage, pieces=report.inspection_pieces or 0, width_text=factory_width_text, total_point=report.total_point, avg_point=report.avg_shipment_point, percentage=report.inspection_percentage, stage=report.inspection_stage or 'Final', status=report.is_pass)
    holder = Table([[left, right]], colWidths=[136*mm, 136*mm], hAlign='LEFT')
    holder.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    story += [holder]
    return story, True


BUILDERS = {
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
    plm = _plm(db, plm_id)
    if not plm:
        raise LookupError('PLM not found')
    builder = BUILDERS.get(module)
    if not builder:
        raise KeyError(f'Unsupported report module: {module}')
    result = builder(db, plm)
    if not result:
        raise ValueError('Report data is not ready yet')
    story, is_landscape = result
    buffer = BytesIO()
    doc = _doc(buffer, landscape_page=is_landscape)
    doc.build(story)
    return buffer.getvalue()
