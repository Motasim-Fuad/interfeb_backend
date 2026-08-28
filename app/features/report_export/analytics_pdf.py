from io import BytesIO

from reportlab.lib.units import mm
from reportlab.platypus import Spacer

from app.features.analytics import service as analytics_service
from app.features.report_export.service import _banner, _doc, _fmt, _p, _table


def _period_meta(title, period, extra=""):
    page_width = 210*mm - 16*mm
    rows = [
        ["Period", period, "Prepared by", "Interfab QC Dashboard"],
    ]
    if extra:
        rows.append(["Filter", extra, "", ""])
    widths = [28*mm, page_width/2 - 28*mm, 28*mm, page_width/2 - 28*mm]
    return [_banner(title, page_width), Spacer(1, 4*mm), _table(rows, widths=widths, header_rows=0), Spacer(1, 5*mm)]


def _empty(message):
    return [_p(message)]


def _quality_table(rows, party_label):
    if not rows:
        return _empty(f"No 4-point quality data for this {party_label.lower()} filter.")
    data = [[party_label, "Rolls checked", "Passed", "Failed", "Pass rate"]]
    for row in rows:
        rate = f"{row['pass_rate']}%" if row.get("pass_rate") is not None else "-"
        data.append([row["name"], row["total"], row["passed"], row["failed"], rate])
    return [_table(data, header_rows=1)]


def _defect_table(rows, party_label):
    if not rows:
        return _empty(f"No defect points recorded for this {party_label.lower()} filter.")
    data = [[party_label, "Defect count", "Total points"]]
    for row in rows:
        data.append([row["name"], row["total_count"], row["total_points"]])
    return [_table(data, header_rows=1)]


def _qty_table(rows, party_label):
    if not rows:
        return _empty(f"No PLM quantity data for this {party_label.lower()} filter.")
    data = [[party_label, "PLMs", "Allocated qty", "Received qty", "Rolls"]]
    for row in rows:
        data.append([row["name"], row["plms"], _fmt(row["allocated"]), _fmt(row["received"]), row["rolls"]])
    return [_table(data, header_rows=1)]


def _comm_table(rows):
    if not rows:
        return _empty("No communication ratings saved for this month yet. Enter them on Analytics.")
    data = [["Supplier", "Month", "Rating / 5", "Notes"]]
    for row in rows:
        rating = _fmt(row["rating"], 1) if row.get("rating") is not None else "-"
        data.append([row["name"], row["period"], rating, row["notes"] or "-"])
    return [_table(data, header_rows=1)]


def build_analysis_pdf(db, kind: str, *, year=None, month=None, buyer_id=None, supplier_id=None) -> bytes:
    date_from, date_to = analytics_service.month_bounds(year, month)
    period = analytics_service.period_label(year, month)
    extra_bits = []
    if buyer_id:
        extra_bits.append("Buyer filtered")
    if supplier_id:
        extra_bits.append("Supplier filtered")
    extra = ", ".join(extra_bits)

    story = []
    if kind == "supplier-quality":
        story = _period_meta("Supplier-wise Fabric Quality", period, extra)
        story += _quality_table(analytics_service.supplier_quality_rows(db, buyer_id=buyer_id, supplier_id=supplier_id, date_from=date_from, date_to=date_to), "Supplier")
    elif kind == "supplier-defects":
        story = _period_meta("Supplier-wise Fabric Defects", period, extra)
        story += _defect_table(analytics_service.defect_rows(db, group="supplier", buyer_id=buyer_id, supplier_id=supplier_id, date_from=date_from, date_to=date_to), "Supplier")
    elif kind == "supplier-quantity":
        story = _period_meta("Supplier-wise Fabric Quantity", period, extra)
        story += _qty_table(analytics_service.quantity_rows(db, group="supplier", buyer_id=buyer_id, supplier_id=supplier_id, date_from=date_from, date_to=date_to), "Supplier")
    elif kind == "supplier-communication":
        story = _period_meta("Supplier-wise Communication Rating", period, extra)
        story += _comm_table(analytics_service.communication_rows(db, year=year, month=month, supplier_id=supplier_id))
    elif kind == "buyer-quality":
        story = _period_meta("Buyer-wise Fabric Quality", period, extra)
        story += _quality_table(analytics_service.buyer_quality_rows(db, buyer_id=buyer_id, supplier_id=supplier_id, date_from=date_from, date_to=date_to), "Buyer")
    elif kind == "buyer-defects":
        story = _period_meta("Buyer-wise Fabric Defects", period, extra)
        story += _defect_table(analytics_service.defect_rows(db, group="buyer", buyer_id=buyer_id, supplier_id=supplier_id, date_from=date_from, date_to=date_to), "Buyer")
    elif kind == "buyer-quantity":
        story = _period_meta("Buyer-wise Fabric Quantity", period, extra)
        story += _qty_table(analytics_service.quantity_rows(db, group="buyer", buyer_id=buyer_id, supplier_id=supplier_id, date_from=date_from, date_to=date_to), "Buyer")
    elif kind == "monthly-dashboard":
        if not year or not month:
            raise ValueError("Monthly dashboard needs year and month")
        data = analytics_service.monthly_dashboard(db, year=year, month=month, buyer_id=buyer_id, supplier_id=supplier_id)
        story = _period_meta("Monthly QC Dashboard", period, extra)
        story += [_p("1. Fabric quality by supplier"), Spacer(1, 2*mm)]
        story += _quality_table(data["quality"], "Supplier")
        story += [Spacer(1, 5*mm), _p("2. Fabric defects by supplier"), Spacer(1, 2*mm)]
        story += _defect_table(data["defects"], "Supplier")
        story += [Spacer(1, 5*mm), _p("3. Fabric quantity by supplier"), Spacer(1, 2*mm)]
        story += _qty_table(data["quantity"], "Supplier")
        story += [Spacer(1, 5*mm), _p("4. Communication rating"), Spacer(1, 2*mm)]
        story += _comm_table(data["communication"])
    else:
        raise KeyError(kind)

    buffer = BytesIO()
    doc = _doc(buffer, landscape_page=False)
    doc.build(story)
    return buffer.getvalue()


ANALYSIS_KINDS = {
    "supplier-quality",
    "supplier-defects",
    "supplier-quantity",
    "supplier-communication",
    "buyer-quality",
    "buyer-defects",
    "buyer-quantity",
    "monthly-dashboard",
}
