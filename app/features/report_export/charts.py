"""Small printable charts for QC PDFs. Keep sizes fixed so tables never overflow."""
from __future__ import annotations

from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import Flowable

PASS_GREEN = colors.HexColor('#2E7D32')
FAIL_RED = colors.HexColor('#C62828')
PENDING_GREY = colors.HexColor('#9CA3AF')
INK = colors.HexColor('#111827')
PAPER = colors.white
LINE = colors.HexColor('#D1D5DB')
NAVY = colors.HexColor('#1E3A5F')


def _safe_int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _clip(text, limit=12) -> str:
    raw = str(text or '').strip()
    if len(raw) <= limit:
        return raw or '—'
    return raw[: max(1, limit - 1)] + '…'


class OverviewPie(Flowable):
    """Doughnut card: title on top, chart in the middle, Pass/Fail/Pend legend below."""

    def __init__(self, passed=0, failed=0, pending=0, size=46*mm, title='Overview'):
        super().__init__()
        self.passed = _safe_int(passed)
        self.failed = _safe_int(failed)
        self.pending = _safe_int(pending)
        self.title = str(title or 'Overview')
        self._wanted = float(size)
        pie = min(32*mm, max(24*mm, self._wanted * 0.58))
        self._pie = pie
        self.width = max(self._wanted, 48*mm)
        # title band + gap + doughnut + gap + legend — never overlap
        self.height = 16 + 6 + pie + 8 + 16

    def wrap(self, availWidth, availHeight):
        if availWidth:
            self.width = min(self.width, float(availWidth))
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.saveState()
        w, h = self.width, self.height
        c.setStrokeColor(LINE)
        c.setLineWidth(0.7)
        c.setFillColor(PAPER)
        c.roundRect(0.5, 0.5, w - 1, h - 1, 3.2, stroke=1, fill=1)

        c.setFillColor(INK)
        c.setFont('Helvetica-Bold', 8)
        c.drawCentredString(w / 2, h - 14, self.title)

        total = self.passed + self.failed + self.pending
        legend_top = 18
        title_bottom = h - 18
        radius = min(self._pie / 2, max(10, (title_bottom - legend_top) / 2 - 1))
        cx = w / 2
        cy = legend_top + (title_bottom - legend_top) / 2

        if total <= 0:
            c.setFillColor(colors.HexColor('#F3F4F6'))
            c.setStrokeColor(LINE)
            c.setLineWidth(0.6)
            c.circle(cx, cy, radius, stroke=1, fill=1)
            c.setFillColor(INK)
            c.setFont('Helvetica', 6.5)
            c.drawCentredString(cx, cy - 2, 'No data')
        else:
            start = 90
            slices = (
                (self.passed, PASS_GREEN),
                (self.failed, FAIL_RED),
                (self.pending, PENDING_GREY),
            )
            for count, color in slices:
                if count <= 0:
                    continue
                extent = 360.0 * count / total
                c.setFillColor(color)
                c.setStrokeColor(PAPER)
                c.setLineWidth(0.8)
                c.wedge(cx - radius, cy - radius, cx + radius, cy + radius, start, extent, stroke=1, fill=1)
                start += extent
            hole = radius * 0.55
            c.setFillColor(PAPER)
            c.setStrokeColor(PAPER)
            c.circle(cx, cy, hole, stroke=0, fill=1)
            c.setFillColor(INK)
            c.setFont('Helvetica-Bold', 9)
            c.drawCentredString(cx, cy - 3.2, str(total))

        items = (('Pass', PASS_GREEN, self.passed), ('Fail', FAIL_RED, self.failed), ('Pend', PENDING_GREY, self.pending))
        gap = (w - 8) / 3
        c.setFont('Helvetica', 5.6)
        for i, (label, color, count) in enumerate(items):
            x = 6 + i * gap
            c.setFillColor(color)
            c.circle(x + 3.2, 8.2, 2.2, stroke=0, fill=1)
            c.setFillColor(INK)
            c.drawString(x + 7.2, 6.2, f'{label} {count}')
        c.restoreState()


def _drawing(width, height) -> Drawing:
    d = Drawing(width, height)
    d.add(Rect(0, 0, width, height, fillColor=PAPER, strokeColor=LINE, strokeWidth=0.5, rx=2, ry=2))
    return d


def grouped_pass_fail_chart(labels, passed, failed, *, width=180*mm, height=58*mm, title=''):
    names = [_clip(x) for x in labels[:8]]
    p = [float(x or 0) for x in passed[:8]]
    f = [float(x or 0) for x in failed[:8]]
    if not names:
        return OverviewPie(0, 0, 0, size=min(width, 40*mm), title=title or 'Overview')
    d = _drawing(width, height)
    if title:
        d.add(String(8, height - 11, title, fontName='Helvetica-Bold', fontSize=8, fillColor=INK))
    chart = VerticalBarChart()
    chart.x = 28
    chart.y = 16
    chart.width = max(40, width - 40)
    chart.height = height - (32 if title else 24)
    chart.data = [p, f]
    chart.barWidth = 6
    chart.groupSpacing = 8
    chart.barSpacing = 1.5
    chart.valueAxis.valueMin = 0
    peak = max(p + f + [1])
    chart.valueAxis.valueMax = peak * 1.2
    chart.valueAxis.visibleGrid = True
    chart.valueAxis.gridStrokeColor = colors.HexColor('#E5E7EB')
    chart.valueAxis.labels.fontSize = 6
    chart.valueAxis.labels.fillColor = INK
    chart.categoryAxis.categoryNames = names
    chart.categoryAxis.labels.fontSize = 6
    chart.categoryAxis.labels.fillColor = INK
    chart.categoryAxis.labels.angle = 25
    chart.categoryAxis.labels.dy = -6
    chart.categoryAxis.labels.boxAnchor = 'ne'
    chart.bars[0].fillColor = PASS_GREEN
    chart.bars[0].strokeColor = PASS_GREEN
    chart.bars[1].fillColor = FAIL_RED
    chart.bars[1].strokeColor = FAIL_RED
    d.add(chart)
    return d


def column_chart(labels, values, *, width=180*mm, height=58*mm, title='', fill=NAVY):
    names = [_clip(x) for x in labels[:8]]
    vals = [float(x or 0) for x in values[:8]]
    if not names:
        return OverviewPie(0, 0, 0, size=min(width, 40*mm), title=title or 'Overview')
    d = _drawing(width, height)
    if title:
        d.add(String(8, height - 11, title, fontName='Helvetica-Bold', fontSize=8, fillColor=INK))
    chart = VerticalBarChart()
    chart.x = 28
    chart.y = 16
    chart.width = max(40, width - 40)
    chart.height = height - (32 if title else 24)
    chart.data = [vals]
    chart.barWidth = 10
    chart.groupSpacing = 10
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = max(vals + [1]) * 1.2
    chart.valueAxis.visibleGrid = True
    chart.valueAxis.gridStrokeColor = colors.HexColor('#E5E7EB')
    chart.valueAxis.labels.fontSize = 6
    chart.valueAxis.labels.fillColor = INK
    chart.categoryAxis.categoryNames = names
    chart.categoryAxis.labels.fontSize = 6
    chart.categoryAxis.labels.fillColor = INK
    chart.categoryAxis.labels.angle = 25
    chart.categoryAxis.labels.dy = -6
    chart.categoryAxis.labels.boxAnchor = 'ne'
    chart.bars[0].fillColor = fill
    chart.bars[0].strokeColor = fill
    d.add(chart)
    return d


def row_chart(labels, values, *, width=180*mm, height=62*mm, title='', fill=NAVY):
    names = [_clip(x, 16) for x in labels[:8]]
    vals = [float(x or 0) for x in values[:8]]
    if not names:
        return OverviewPie(0, 0, 0, size=min(width, 40*mm), title=title or 'Overview')
    d = _drawing(width, height)
    if title:
        d.add(String(8, height - 11, title, fontName='Helvetica-Bold', fontSize=8, fillColor=INK))
    chart = HorizontalBarChart()
    chart.x = 62
    chart.y = 10
    chart.width = max(40, width - 78)
    chart.height = height - (28 if title else 20)
    chart.data = [list(reversed(vals))]
    chart.barWidth = 7
    chart.groupSpacing = 6
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = max(vals + [1]) * 1.15
    chart.valueAxis.visibleGrid = True
    chart.valueAxis.gridStrokeColor = colors.HexColor('#E5E7EB')
    chart.valueAxis.labels.fontSize = 6
    chart.valueAxis.labels.fillColor = INK
    chart.categoryAxis.categoryNames = list(reversed(names))
    chart.categoryAxis.labels.fontSize = 6.2
    chart.categoryAxis.labels.fillColor = INK
    chart.bars[0].fillColor = fill
    chart.bars[0].strokeColor = fill
    d.add(chart)
    return d
