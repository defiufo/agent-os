"""Branded PDF rendering of the Pilot Router savings report."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from agentos.observability.savings_pdf import render_savings_pdf
from agentos.observability.savings_report import DayRow, RouteRow, SavingsReport


def _report(**overrides: object) -> SavingsReport:
    base = SavingsReport(
        start_date="2026-08-18",
        end_date="2026-09-01",
        turns_total=274,
        turns_routed=260,
        turns_rerouted=157,
        turns_kept=103,
        turns_at_top_tier=98,
        actual_cost_usd=17.401,
        routing_savings_usd=34.7016,
        top_tier_cost_usd=52.1026,
        savings_pct=66.6,
        avg_confidence=0.5177,
        tokens_input=1_234_567,
        tokens_output=45_678,
        by_route=[
            RouteRow("gpt-5.6-luna", "glm-5.2", 103, 20.5, 21.0, 0.52),
            RouteRow("gpt-5.6-luna", "deepseek-v4-flash", 39, 12.2, 64.0, 0.61),
        ],
        by_day=[
            DayRow("2026-08-18", 30, 4.5, 2.1),
            DayRow("2026-09-01", 8, 1.2, 0.6),
        ],
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _text(path: Path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def _flat(path: Path) -> str:
    """Page text with all whitespace collapsed, so line wrapping cannot hide a phrase."""

    return " ".join(_text(path).split())


def test_pdf_is_written_and_readable(tmp_path: Path) -> None:
    out = tmp_path / "savings.pdf"

    render_savings_pdf(_report(), out)

    assert out.read_bytes().startswith(b"%PDF-")
    assert len(PdfReader(str(out)).pages) >= 1


def test_pdf_carries_the_agentos_wordmark(tmp_path: Path) -> None:
    out = tmp_path / "savings.pdf"

    render_savings_pdf(_report(), out)

    assert "agentOS_" in _text(out)


def test_pdf_states_the_headline_savings_and_window(tmp_path: Path) -> None:
    out = tmp_path / "savings.pdf"

    render_savings_pdf(_report(), out)
    text = _text(out)

    assert "$34.70" in text
    assert "66.6%" in text
    assert "2026-08-18" in text
    assert "2026-09-01" in text


def test_pdf_states_that_the_comparison_is_the_top_tier_on_input_tokens(
    tmp_path: Path,
) -> None:
    out = tmp_path / "savings.pdf"

    render_savings_pdf(_report(), out)
    text = _flat(out)

    # The figure is not "vs the requested model" and not a full-turn price;
    # a reader must be able to see both limits on the page itself.
    assert "most expensive model configured in [router.tiers]" in text
    assert "Only input tokens are priced" in text


def test_pdf_lists_each_route_pair(tmp_path: Path) -> None:
    out = tmp_path / "savings.pdf"

    render_savings_pdf(_report(), out)
    text = _text(out)

    assert "glm-5.2" in text
    assert "deepseek-v4-flash" in text
    assert "gpt-5.6-luna" in text


def test_pdf_renders_an_empty_report_without_crashing(tmp_path: Path) -> None:
    empty = SavingsReport(
        start_date=None,
        end_date=None,
        turns_total=0,
        turns_routed=0,
        turns_rerouted=0,
        turns_kept=0,
        turns_at_top_tier=0,
        actual_cost_usd=0.0,
        routing_savings_usd=0.0,
        top_tier_cost_usd=0.0,
        savings_pct=0.0,
        avg_confidence=None,
        tokens_input=0,
        tokens_output=0,
    )
    out = tmp_path / "empty.pdf"

    render_savings_pdf(empty, out)

    assert out.read_bytes().startswith(b"%PDF-")
    assert "No routed turns" in _text(out)


def test_pdf_paginates_a_long_route_table(tmp_path: Path) -> None:
    many = [RouteRow("gpt-5.6-luna", f"model-{i:03d}", 3, 1.0, 20.0, 0.5) for i in range(60)]
    out = tmp_path / "long.pdf"

    render_savings_pdf(_report(by_route=many), out)

    assert len(PdfReader(str(out)).pages) >= 2
    assert "model-059" in _text(out)


def _fits(text: str, font: str, size: float, width: float) -> bool:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    return bool(stringWidth(text, font, size) <= width)


def test_stat_labels_fit_inside_their_column(tmp_path: Path) -> None:
    """Six stats share the text column; a long label must not run into the next."""

    from reportlab.lib.pagesizes import A4

    from agentos.observability.savings_pdf import (
        _PAGE_MARGIN,
        _STAT_LABEL_SIZE,
        _stat_cells,
    )

    cells = _stat_cells(_report())
    column = (A4[0] - 2 * _PAGE_MARGIN) / len(cells)

    for label, _value in cells:
        assert _fits(label, "Helvetica", _STAT_LABEL_SIZE, column - 6), label


def test_header_title_halves_do_not_overlap(tmp_path: Path) -> None:
    """The two-weight title is laid out by measurement, not a fixed offset."""

    from reportlab.pdfbase.pdfmetrics import stringWidth

    from agentos.observability.savings_pdf import _TITLE_LEAD, _TITLE_SIZE, _TITLE_TAIL

    lead = stringWidth(_TITLE_LEAD, "Helvetica-Bold", _TITLE_SIZE)
    out = tmp_path / "savings.pdf"
    render_savings_pdf(_report(), out)

    # The tail starts after the lead plus a real space, so the strings are
    # separated in the extracted text rather than run together.
    assert f"{_TITLE_LEAD} {_TITLE_TAIL}" in " ".join(_text(out).split())
    assert lead > 0


def test_a_dozen_route_rows_still_fit_on_one_page(tmp_path: Path) -> None:
    rows = [RouteRow("gpt-5.6-luna", f"model-{i:02d}", 3, 1.0, 20.0, 0.5) for i in range(12)]
    out = tmp_path / "one-page.pdf"

    render_savings_pdf(_report(by_route=rows), out)

    assert len(PdfReader(str(out)).pages) == 1
