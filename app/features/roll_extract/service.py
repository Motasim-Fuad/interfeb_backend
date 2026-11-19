"""Reads a supplier mill report (photo, scan, handwritten sheet, PDF, Excel or CSV) into roll rows.

Only five things are kept per roll: colour, roll no, width, length and total defect points;
every other column on the sheet is ignored. Nothing is saved here: the Dashboard shows the
rows for review and then sends them through the normal roll import (dry run first).

Photos and PDF pages go to an OpenAI-compatible vision model (the local Ollama service by
default). Excel / CSV are read directly and never need the model.
"""
from __future__ import annotations

import base64
import csv
import io
import json
import logging
import re
from typing import Iterable, Optional

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

IMAGE_TYPES = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff"}
SHEET_TYPES = {"xlsx", "xlsm", "csv"}
MAX_PDF_PAGES = 12

_ALIASES = {
    "color": ("color", "colour", "color code", "colour code", "shade", "col"),
    "roll_no": ("roll no", "roll", "roll number", "roll #", "roll no.", "piece no", "pcs no", "roll id"),
    "marked_width": ("width", "cuttable width", "cut width", "wid", "fabric width", "width (inch)", "width (cm)"),
    "marked_length": ("length", "yds", "yards", "yard", "meter", "meters", "mtr", "qty", "quantity", "length in yards", "length (yds)"),
    "supplier_total_point": ("total point", "total points", "points", "point", "pts", "defect point", "defect points", "total defect point", "total defect points"),
}


class ExtractError(Exception):
    pass


def _norm(text) -> str:
    return " ".join(re.sub(r"[^a-z0-9#.]+", " ", str(text or "").lower()).split())


def _num(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(value))
    return float(match.group().replace(",", ".")) if match else None


def _roll_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


_UNSURE_KEYS = (("width", "width"), ("length", "length"), ("yd", "length"), ("meter", "length"), ("point", "points"),
                ("pts", "points"), ("defect", "points"), ("roll", "roll_no"), ("colo", "color"), ("shade", "color"))


def _unsure_field(label: str) -> Optional[str]:
    key = str(label or "").lower()
    return next((field for needle, field in _UNSURE_KEYS if needle in key), None)


def _clean_rows(raw: Iterable[dict], source: str) -> list[dict]:
    """Keep only rows that have a roll number; drop totals/headers the reader picked up."""
    out = []
    for r in raw:
        roll = _roll_text(r.get("roll_no"))
        if not roll or _norm(roll) in ("total", "grand total", "sub total", "roll no", "roll"):
            continue
        unsure = {u for u in (_unsure_field(f) for f in (r.get("unsure") or []) if isinstance(f, str)) if u}
        row = {
            "color": str(r.get("color") or "").strip(),
            "roll_no": roll,
            "marked_width": _num(r.get("marked_width", r.get("width"))),
            "marked_length": _num(r.get("marked_length", r.get("length"))),
            "supplier_total_point": _num(r.get("supplier_total_point", r.get("points"))),
            "unsure": unsure,
            "source": source,
        }
        # A width/length of 0 is never real: treat it as unread and ask for a check.
        for field, ui in (("marked_width", "width"), ("marked_length", "length")):
            if row[field] is not None and row[field] <= 0:
                row[field] = None
        for field, ui in (("marked_width", "width"), ("marked_length", "length"), ("supplier_total_point", "points")):
            if row[field] is None:
                unsure.add(ui)
        out.append(row)
    # A model that marks the same column unsure on every row is just being cautious, not
    # pointing at a bad cell; keep flags only where a value is actually missing.
    if len(out) >= 3:
        for field in ("width", "length", "points", "roll_no", "color"):
            if all(field in r["unsure"] for r in out):
                key = {"width": "marked_width", "length": "marked_length", "points": "supplier_total_point"}.get(field, field)
                for r in out:
                    if r.get(key) not in (None, ""):
                        r["unsure"].discard(field)
    for r in out:
        r["unsure"] = sorted(r["unsure"])
    return out


# ----------------------------------------------------------------- spreadsheets (no model)

def _header_map(cells: list) -> dict:
    found = {}
    for i, cell in enumerate(cells):
        key = _norm(cell)
        if not key:
            continue
        for field, names in _ALIASES.items():
            if field not in found and (key in names or any(key.startswith(n + " ") for n in names)):
                found[field] = i
                break
    return found if "roll_no" in found else {}


def _rows_from_table(table: list[list], source: str) -> list[dict]:
    for h, cells in enumerate(table[:30]):  # header can sit below a title block
        cols = _header_map(cells)
        if cols:
            raw = []
            for cells2 in table[h + 1:]:
                get = lambda f: cells2[cols[f]] if f in cols and cols[f] < len(cells2) else None
                raw.append({f: get(f) for f in _ALIASES})
            return _clean_rows(raw, source)
    raise ExtractError(f"{source}: no 'Roll No' column found.")


def read_spreadsheet(name: str, data: bytes) -> list[dict]:
    ext = name.rsplit(".", 1)[-1].lower()
    if ext == "csv":
        text = data.decode("utf-8-sig", errors="replace")
        return _rows_from_table([row for row in csv.reader(io.StringIO(text))], name)
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    rows, errors = [], []
    for ws in wb.worksheets:
        table = [list(r) for r in ws.iter_rows(values_only=True)]
        try:
            rows += _rows_from_table(table, f"{name} / {ws.title}")
        except ExtractError as exc:
            errors.append(str(exc))
    if not rows and errors:
        raise ExtractError(errors[0])
    return rows


# ----------------------------------------------------------------- images / PDF (vision model)

PROMPT = (
    "This image is a fabric inspection or mill report table (printed, scanned or handwritten). "
    "Read EVERY roll row in the table, from the first to the last (a sheet can have 100 or more rows; "
    "return all of them, never just one). For each roll copy these values exactly as written:\n"
    "- color: the colour / colour code / shade of the roll (if written)\n"
    "- roll_no: the roll number\n"
    "- width: the fabric width (if there are Min/Max or Reqr/Actual width columns, use the first written number)\n"
    "- length: the roll length / yards / meters (if there are Marked and Actual length columns, use Marked)\n"
    "- points: the roll's TOTAL defect points (the 'Total Points' / 'Total' column, not each defect)\n"
    "Ignore every other column. Read the handwritten digits carefully, including decimals like 174.9. "
    "Write a number only if you can see it on the sheet; otherwise null. Never write 0 for an empty cell. "
    "Skip total/summary rows. If you are not sure of a value, still write your best reading and add the "
    "field name (width, length, points, roll_no or color) to \"unsure\".\n"
    'Answer with JSON only, one object per roll, in sheet order. Shape (values here are only an example): '
    '{"rows":[{"color":"A1","roll_no":"1","width":58.5,"length":101.2,"points":14,"unsure":[]},'
    '{"color":"A1","roll_no":"2","width":58.5,"length":96.0,"points":9,"unsure":["points"]}]}'
)


def _jpeg(data: bytes) -> bytes:
    from PIL import Image, ImageOps

    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail((1600, 1600))
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=88)
    return out.getvalue()


def _pdf_pages(data: bytes) -> list[bytes]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    pages = []
    for i in range(min(len(pdf), MAX_PDF_PAGES)):
        bitmap = pdf[i].render(scale=2.0).to_pil()
        buf = io.BytesIO()
        bitmap.convert("RGB").save(buf, format="JPEG", quality=88)
        pages.append(buf.getvalue())
    return pages


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ExtractError("The model did not return a table.")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ExtractError(f"Could not read the model answer ({exc.msg}).") from exc


def _readers() -> list[dict]:
    """Main reader first (e.g. Gemini), then the local fallback. A reader needs a URL and a model;
    a cloud URL without an API key is skipped so it falls straight through to the local one."""
    out = []
    for prefix in ("VISION_LLM", "VISION_FALLBACK"):
        url = getattr(settings, f"{prefix}_BASE_URL", "")
        model = getattr(settings, f"{prefix}_MODEL", "")
        key = getattr(settings, f"{prefix}_API_KEY", "")
        local = any(h in url for h in ("ollama", "localhost", "127.0.0.1", "host.docker.internal"))
        if url and model and (key or local):
            # Several models may be listed (comma-separated): a busy or retired one is skipped.
            for name in [m.strip() for m in model.split(",") if m.strip()]:
                out.append({"url": url.rstrip("/"), "model": name, "key": key,
                            "timeout": getattr(settings, f"{prefix}_TIMEOUT_SECONDS"), "local": local})
    return out


def vision_enabled() -> bool:
    return bool(_readers())


def vision_label() -> str:
    readers = _readers()
    return readers[0]["model"] if readers else ""


def _ask(reader: dict, image_jpeg: bytes) -> str:
    headers = {"Content-Type": "application/json"}
    if reader["key"]:
        headers["Authorization"] = f"Bearer {reader['key']}"
    url = "data:image/jpeg;base64," + base64.b64encode(image_jpeg).decode()
    body = {
        "model": reader["model"],
        "temperature": 0,
        "max_tokens": 16000,  # ~200 rolls per page
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": url}},
        ]}],
    }
    res = httpx.post(f"{reader['url']}/chat/completions", headers=headers, json=body, timeout=reader["timeout"])
    res.raise_for_status()
    return res.json()["choices"][0]["message"].get("content") or ""


def read_image(image_jpeg: bytes, source: str, progress=None) -> list[dict]:
    readers = _readers()
    if not readers:
        raise ExtractError("Photo reading is off: set VISION_LLM_* (or VISION_FALLBACK_*) in .env.")
    last_error = ""
    for i, reader in enumerate(readers):
        if i and progress:
            if reader["local"] and not readers[i - 1]["local"]:
                progress(f"{source}: main reader unavailable, using the local AI (slower)...")
            else:
                progress(f"{source}: {readers[i - 1]['model']} is busy, trying {reader['model']}...")
        try:
            data = _parse_json(_ask(reader, image_jpeg))
        except (httpx.HTTPError, KeyError, IndexError, ValueError, ExtractError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            last_error = f"{reader['model']}: {status or exc.__class__.__name__}"
            log.warning("Vision reader %s failed for %s: %s", reader["model"], source, last_error)
            continue
        rows = data.get("rows") if isinstance(data, dict) else None
        if isinstance(rows, list):
            return _clean_rows([r for r in rows if isinstance(r, dict)], source)
        last_error = f"{reader['model']}: no table in answer"
    raise ExtractError(f"{source}: could not be read ({last_error}). Check the photo is sharp, or try again.")


def extract_file(name: str, data: bytes, progress=None) -> list[dict]:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in SHEET_TYPES:
        return read_spreadsheet(name, data)
    if ext == "pdf":
        pages = _pdf_pages(data)
        rows = []
        for i, page in enumerate(pages, 1):
            if progress:
                progress(f"{name}: page {i} of {len(pages)}")
            rows += read_image(page, f"{name} p{i}", progress)
        return rows
    if ext in IMAGE_TYPES:
        return read_image(_jpeg(data), name, progress)
    raise ExtractError(f"{name}: use a photo (JPG/PNG), PDF, Excel (.xlsx) or CSV file.")
