"""Render a :class:`~agentos.observability.savings_report.SavingsReport` as a
branded, shareable PDF.

Drawn straight onto a ReportLab canvas rather than assembled with Platypus:
the page is a fixed one-off layout, not a flowing document, and the AgentOS
mark is vector geometry (four discs and three spokes) that has to sit exactly
in the header corner. ``reportlab`` is a base dependency, so no extra install
is needed.

Palette follows the product brand — near-black ink, lime ``#ccff00`` accent —
on a white ground so the report stays printable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from math import cos, pi, sin
from pathlib import Path
from typing import Any

from agentos.observability.savings_report import SavingsReport

# Brand palette. LIME is the same accent the Web UI uses (`--color-accent`).
_LIME = (0.8, 1.0, 0.0)
_INK = (0.063, 0.078, 0.094)
_MUTED = (0.42, 0.45, 0.49)
_HAIRLINE = (0.85, 0.87, 0.89)
_TINT = (0.965, 0.984, 0.898)
_WHITE = (1.0, 1.0, 1.0)

_PAGE_MARGIN = 44.0
_HEADER_HEIGHT = 92.0
_FOOTER_HEIGHT = 38.0

#: Route-table rows that fit below the chart on page 1, and on a full page.
_ROUTE_ROWS_FIRST_PAGE = 14
_ROUTE_ROWS_PER_PAGE = 30
_ROW_HEIGHT = 17.0

#: Header title, split so the two halves can carry different weights. Laid out
#: by measuring the lead rather than by a hand-tuned offset.
_TITLE_LEAD = "Pilot Router"
_TITLE_TAIL = "Cost Savings Report"
_TITLE_SIZE = 19.0

#: Stat-grid type sizes. Labels must fit their share of the text column - see
#: ``test_stat_labels_fit_inside_their_column``.
_STAT_VALUE_SIZE = 15.0
_STAT_LABEL_SIZE = 7.5


def render_savings_pdf(
    report: SavingsReport,
    path: Path,
    *,
    generated_at: datetime | None = None,
) -> Path:
    """Write ``report`` to ``path`` as a PDF and return the path."""

    from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
    from reportlab.pdfgen import canvas as pdfcanvas  # type: ignore[import-untyped]

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    width, height = A4
    stamp = (generated_at or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC")

    c = pdfcanvas.Canvas(str(path), pagesize=A4)
    c.setTitle("AgentOS Pilot Router - Cost Savings Report")
    c.setAuthor("AgentOS")
    c.setSubject("Estimated model-routing cost savings vs the most expensive configured tier")

    _draw_header(c, width, height, subtitle=_window_label(report))
    y = height - _HEADER_HEIGHT - 28.0
    y = _draw_hero(c, width, y, report)
    y = _draw_stat_grid(c, width, y, report)
    y = _draw_daily_chart(c, width, y, report)
    remaining = _draw_route_table(c, width, y, report.by_route, limit=_ROUTE_ROWS_FIRST_PAGE)
    _draw_footer(c, width, stamp)

    while remaining:
        c.showPage()
        _draw_header(c, width, height, subtitle=_window_label(report))
        y = height - _HEADER_HEIGHT - 28.0
        remaining = _draw_route_table(c, width, y, remaining, limit=_ROUTE_ROWS_PER_PAGE)
        _draw_footer(c, width, stamp)

    c.save()
    return path


def _window_label(report: SavingsReport) -> str:
    if report.start_date and report.end_date:
        return f"{report.start_date} to {report.end_date}"
    return "no data in window"


# --------------------------------------------------------------------------
# Chrome
# --------------------------------------------------------------------------


def _draw_header(c: Any, width: float, height: float, *, subtitle: str) -> None:
    """Black band with the title on the left and the AgentOS mark on the right."""

    c.setFillColorRGB(*_INK)
    c.rect(0, height - _HEADER_HEIGHT, width, _HEADER_HEIGHT, stroke=0, fill=1)

    c.setFillColorRGB(*_LIME)
    c.rect(0, height - _HEADER_HEIGHT, width, 3, stroke=0, fill=1)

    from reportlab.pdfbase.pdfmetrics import stringWidth  # type: ignore[import-untyped]

    c.setFillColorRGB(*_WHITE)
    c.setFont("Helvetica-Bold", _TITLE_SIZE)
    c.drawString(_PAGE_MARGIN, height - 46, _TITLE_LEAD)
    lead_width = stringWidth(_TITLE_LEAD + " ", "Helvetica-Bold", _TITLE_SIZE)
    c.setFont("Helvetica", _TITLE_SIZE)
    c.drawString(_PAGE_MARGIN + lead_width, height - 46, _TITLE_TAIL)

    c.setFillColorRGB(*_LIME)
    c.setFont("Helvetica", 9.5)
    c.drawString(_PAGE_MARGIN, height - 64, subtitle)

    _draw_logo(c, width - _PAGE_MARGIN - 27, height - _HEADER_HEIGHT / 2 + 6, 27)


def _draw_logo(c: Any, cx: float, cy: float, radius: float) -> None:
    """Draw the AgentOS mark centred on ``(cx, cy)`` with the wordmark below.

    The mark is a hub disc with three spokes to satellite discs at 90 deg,
    210 deg and 330 deg — the same geometry as ``assets/agentos-stacked-logo.png``.
    """

    hub = radius * 0.42
    satellite = radius * 0.20

    c.setStrokeColorRGB(*_LIME)
    c.setFillColorRGB(*_LIME)
    c.setLineWidth(max(1.1, radius * 0.06))

    for degrees in (90.0, 210.0, 330.0):
        angle = degrees * pi / 180.0
        sx = cx + radius * cos(angle)
        sy = cy + radius * sin(angle)
        c.line(cx, cy, sx, sy)
        c.circle(sx, sy, satellite, stroke=0, fill=1)

    c.circle(cx, cy, hub, stroke=0, fill=1)

    c.setFont("Courier-Bold", 8)
    c.drawCentredString(cx, cy - radius - 12, "agentOS_")


#: What the headline number is and is not, printed on every page.
_METHOD_NOTE = (
    "Method: the baseline is the most expensive model configured in [router.tiers] - what every "
    "routed turn would have cost had it gone to your top tier. Only input tokens are priced, at "
    "list rates, so the figure is a floor rather than a full-turn saving. Routing only: "
    "tool-result projection, short-reply enforcement, prompt-cache hits and thinking mode are "
    'excluded. "Requested" names the model the turn asked for, which is not the price '
    "comparison."
)


def _draw_footer(c: Any, width: float, stamp: str) -> None:
    inner = width - 2 * _PAGE_MARGIN
    lines = _wrap(_METHOD_NOTE, "Helvetica", 7.0, inner)

    rule_y = _FOOTER_HEIGHT + 9 * len(lines)
    c.setStrokeColorRGB(*_HAIRLINE)
    c.setLineWidth(0.6)
    c.line(_PAGE_MARGIN, rule_y, width - _PAGE_MARGIN, rule_y)

    c.setFillColorRGB(*_MUTED)
    c.setFont("Helvetica", 7.0)
    text_y = rule_y - 11
    for line in lines:
        c.drawString(_PAGE_MARGIN, text_y, line)
        text_y -= 9

    c.setFont("Helvetica", 7.0)
    c.drawRightString(width - _PAGE_MARGIN, rule_y + 5, f"agentOS - generated {stamp}")


def _wrap(text: str, font: str, size: float, max_width: float) -> list[str]:
    """Greedy word wrap against the real glyph widths of ``font`` at ``size``."""

    from reportlab.pdfbase.pdfmetrics import stringWidth  # type: ignore[import-untyped]

    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and stringWidth(candidate, font, size) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


# --------------------------------------------------------------------------
# Body
# --------------------------------------------------------------------------


def _draw_hero(c: Any, width: float, y: float, report: SavingsReport) -> float:
    """The headline dollar figure, on a lime-tinted card."""

    card_height = 96.0
    top = y
    c.setFillColorRGB(*_TINT)
    c.rect(_PAGE_MARGIN, top - card_height, width - 2 * _PAGE_MARGIN, card_height, stroke=0, fill=1)
    c.setFillColorRGB(*_LIME)
    c.rect(_PAGE_MARGIN, top - card_height, 4, card_height, stroke=0, fill=1)

    c.setFillColorRGB(*_MUTED)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(_PAGE_MARGIN + 20, top - 24, "ESTIMATED SAVED BY ROUTING")

    c.setFillColorRGB(*_INK)
    c.setFont("Helvetica-Bold", 40)
    c.drawString(_PAGE_MARGIN + 20, top - 66, f"${report.routing_savings_usd:,.2f}")

    c.setFont("Helvetica", 10)
    c.setFillColorRGB(*_MUTED)
    if report.turns_routed:
        c.drawString(
            _PAGE_MARGIN + 20,
            top - 84,
            f"{report.savings_pct:.1f}% of a ${report.top_tier_cost_usd:,.2f} top-tier bill "
            f"over {report.turns_routed:,} routed turns",
        )
    else:
        c.drawString(_PAGE_MARGIN + 20, top - 84, "No routed turns in this window")

    # Right-hand pair: what was actually paid vs the top-tier counterfactual.
    right = width - _PAGE_MARGIN - 20
    c.setFillColorRGB(*_MUTED)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawRightString(right, top - 24, "ACTUAL / TOP TIER")
    c.setFillColorRGB(*_INK)
    c.setFont("Helvetica-Bold", 17)
    c.drawRightString(
        right,
        top - 48,
        f"${report.actual_cost_usd:,.2f} / ${report.top_tier_cost_usd:,.2f}",
    )
    c.setFillColorRGB(*_MUTED)
    c.setFont("Helvetica", 9)
    c.drawRightString(right, top - 64, f"{report.savings_pct:.1f}% avoided")

    return top - card_height - 26.0


def _stat_cells(report: SavingsReport) -> list[tuple[str, str]]:
    """The stat-grid labels and values, in display order.

    Labels are kept short on purpose: six cells share the text column, so a
    long one runs into its neighbour.
    """

    confidence = f"{report.avg_confidence:.2f}" if report.avg_confidence is not None else "n/a"
    return [
        ("TURNS LOGGED", f"{report.turns_total:,}"),
        ("ROUTED", f"{report.turns_routed:,}"),
        ("REROUTED", f"{report.turns_rerouted:,}"),
        ("KEPT", f"{report.turns_kept:,}"),
        ("TOP TIER", f"{report.turns_at_top_tier:,}"),
        ("AVG CONF", confidence),
    ]


def _draw_stat_grid(c: Any, width: float, y: float, report: SavingsReport) -> float:
    """Six small stat cells across the page."""

    cells = _stat_cells(report)

    inner = width - 2 * _PAGE_MARGIN
    cell_width = inner / len(cells)
    for index, (label, value) in enumerate(cells):
        x = _PAGE_MARGIN + index * cell_width
        c.setFillColorRGB(*_INK)
        c.setFont("Helvetica-Bold", _STAT_VALUE_SIZE)
        c.drawString(x, y - 16, value)
        c.setFillColorRGB(*_MUTED)
        c.setFont("Helvetica", _STAT_LABEL_SIZE)
        c.drawString(x, y - 28, label)

    c.setStrokeColorRGB(*_HAIRLINE)
    c.setLineWidth(0.6)
    c.line(_PAGE_MARGIN, y - 42, width - _PAGE_MARGIN, y - 42)

    return y - 62.0


def _draw_daily_chart(c: Any, width: float, y: float, report: SavingsReport) -> float:
    """Bar chart of savings per day."""

    chart_height = 108.0
    c.setFillColorRGB(*_INK)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(_PAGE_MARGIN, y, "Saved per day")

    top = y - 14.0
    baseline_y = top - chart_height
    inner = width - 2 * _PAGE_MARGIN

    if not report.by_day:
        c.setFillColorRGB(*_MUTED)
        c.setFont("Helvetica", 9)
        c.drawString(_PAGE_MARGIN, top - 20, "No routed turns to chart.")
        return top - 40.0

    peak = max(row.savings_usd for row in report.by_day)
    peak = peak if peak > 0 else 1.0
    slot = inner / len(report.by_day)
    bar_width = min(26.0, slot * 0.62)

    c.setStrokeColorRGB(*_HAIRLINE)
    c.setLineWidth(0.6)
    c.line(_PAGE_MARGIN, baseline_y, width - _PAGE_MARGIN, baseline_y)

    # Label every Nth day so a long window keeps a readable axis.
    step = max(1, len(report.by_day) // 10)
    for index, row in enumerate(report.by_day):
        centre = _PAGE_MARGIN + index * slot + slot / 2
        bar = max(1.0, (max(row.savings_usd, 0.0) / peak) * (chart_height - 16))
        c.setFillColorRGB(*_LIME)
        c.rect(centre - bar_width / 2, baseline_y, bar_width, bar, stroke=0, fill=1)
        if index % step == 0 or index == len(report.by_day) - 1:
            c.setFillColorRGB(*_MUTED)
            c.setFont("Helvetica", 6.5)
            c.drawCentredString(centre, baseline_y - 10, row.date[5:])

    c.setFillColorRGB(*_MUTED)
    c.setFont("Helvetica", 7.5)
    c.drawRightString(width - _PAGE_MARGIN, top, f"peak ${peak:,.2f}")

    return baseline_y - 34.0


def _draw_route_table(c: Any, width: float, y: float, rows: list[Any], *, limit: int) -> list[Any]:
    """Draw up to ``limit`` route rows; return the rows that did not fit."""

    c.setFillColorRGB(*_INK)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(_PAGE_MARGIN, y, "Where the savings came from")

    header_y = y - 18.0
    columns = _route_columns(width)

    c.setFillColorRGB(*_MUTED)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(columns["requested"], header_y, "REQUESTED")
    c.drawString(columns["routed"], header_y, "ROUTED TO")
    c.drawRightString(columns["turns"], header_y, "TURNS")
    c.drawRightString(columns["pct"], header_y, "AVG %")
    c.drawRightString(columns["confidence"], header_y, "AVG CONF")
    c.drawRightString(columns["saved"], header_y, "SAVED")

    c.setStrokeColorRGB(*_HAIRLINE)
    c.setLineWidth(0.6)
    c.line(_PAGE_MARGIN, header_y - 6, width - _PAGE_MARGIN, header_y - 6)

    if not rows:
        c.setFillColorRGB(*_MUTED)
        c.setFont("Helvetica", 9)
        c.drawString(_PAGE_MARGIN, header_y - 22, "No routed turns in this window.")
        return []

    row_y = header_y - 20.0
    for row in rows[:limit]:
        c.setFillColorRGB(*_INK)
        c.setFont("Helvetica", 8.5)
        c.drawString(columns["requested"], row_y, _clip(row.requested_model, 26))
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(columns["routed"], row_y, _clip(row.routed_model, 26))
        c.setFont("Helvetica", 8.5)
        c.drawRightString(columns["turns"], row_y, f"{row.turns:,}")
        c.drawRightString(
            columns["pct"],
            row_y,
            f"{row.avg_savings_pct:.1f}%" if row.avg_savings_pct is not None else "-",
        )
        c.drawRightString(
            columns["confidence"],
            row_y,
            f"{row.avg_confidence:.2f}" if row.avg_confidence is not None else "-",
        )
        c.setFillColorRGB(*_INK)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawRightString(columns["saved"], row_y, f"${row.savings_usd:,.2f}")
        row_y -= _ROW_HEIGHT

    return list(rows[limit:])


def _route_columns(width: float) -> dict[str, float]:
    right = width - _PAGE_MARGIN
    return {
        "requested": _PAGE_MARGIN,
        "routed": _PAGE_MARGIN + 150,
        "turns": right - 210,
        "pct": right - 150,
        "confidence": right - 80,
        "saved": right,
    }


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
