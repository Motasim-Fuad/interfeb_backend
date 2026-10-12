"""Printable PDF for any FabriQ dataset, in the same letterhead style as the module reports."""
from datetime import datetime
from html import escape
from io import BytesIO

from reportlab.platypus import Paragraph, Spacer

from app.features.report_export.service import CELL, _banner, _doc, _fmt, _page_content_width, _table

_WEIGHTS = {"number": 0.8, "percent": 0.8, "status": 0.9, "grade": 0.6, "date": 1.0, "datetime": 1.3, "text": 1.6}


def _value(row: dict, column: dict) -> str:
    v = row.get(column["key"])
    if v is None or v == "":
        return "-"
    if column["kind"] == "percent":
        return f"{_fmt(v, 1)}%"
    if column["kind"] == "number":
        return _fmt(v)
    if column["kind"] == "datetime":
        return str(v).replace("T", " ")
    return str(v)


def dataset_pdf(page: dict, scope: str, search: str = "", filters: dict | None = None) -> bytes:
    columns = page["columns"]
    landscape_page = len(columns) > 6
    width = _page_content_width(landscape_page=landscape_page)
    weights = [_WEIGHTS.get(c["kind"], 1.2) for c in columns]
    total_w = sum(weights)
    widths = [width * w / total_w for w in weights]

    notes = [f"Scope: {scope or 'All'}", f"Rows: {page['total']}", f"Generated: {datetime.now().strftime('%d-%m-%Y %H:%M')}"]
    if search:
        notes.append(f"Search: \u201c{search}\u201d")
    for k, v in (filters or {}).items():
        if v not in (None, ""):
            notes.append(f"{k.replace('_', ' ').title()}: {v}")

    data = [[c["label"] for c in columns]] + [[_value(r, c) for c in columns] for r in page["rows"]]
    story = [_banner(f"{page['title']} \u2014 FabriQ", width), Spacer(1, 4), Paragraph(" &nbsp;\u2022&nbsp; ".join(escape(n) for n in notes), CELL), Spacer(1, 6)]
    story.append(_table(data, widths, header_rows=1, font=7.0 if len(columns) <= 8 else 6.4))
    buffer = BytesIO()
    _doc(buffer, landscape_page=landscape_page).build(story)
    return buffer.getvalue()
