"""Pilot Router savings aggregation over the decision log."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentos.observability.savings_report import build_savings_report


def _turn(
    date: str,
    *,
    routed: str | None = "glm-5.2",
    requested: str | None = "gpt-5.6-luna",
    savings_usd: float | None = 0.02,
    savings_pct: float | None = 20.5,
    cost_usd: float | None = 0.08,
    confidence: float | None = 0.5,
    tokens_input: int = 1000,
    tokens_output: int = 100,
) -> dict[str, Any]:
    return {
        "turn_id": f"t-{date}-{routed}-{savings_usd}",
        "session_key": "s1",
        "prompt_hash": "a" * 16,
        "system_prompt_hash": "b" * 16,
        "tool_list_hash": "c" * 16,
        "tool_choice": "auto",
        "tokens_input": tokens_input,
        "tokens_output": tokens_output,
        "model": routed or "gpt-5.6-luna",
        "provider": "openrouter",
        "latency_ms": 1200,
        "ts": f"{date}T10:00:00Z",
        "savings": {
            "routed_model": routed,
            "baseline_model": requested,
            "routing_confidence": confidence,
            "routing_savings_pct": savings_pct,
            "routing_savings_usd_estimated_vs_baseline": savings_usd,
            "cost_usd": cost_usd,
        },
    }


def _write_log(log_dir: Path, date: str, rows: list[dict[str, Any]]) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    compact = date.replace("-", "")
    path = log_dir / f"decisions-{compact}.jsonl"
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_totals_ignore_turns_without_routing_savings(tmp_path: Path) -> None:
    _write_log(
        tmp_path,
        "2026-09-01",
        [
            _turn("2026-09-01", savings_usd=0.02, cost_usd=0.08),
            _turn("2026-09-01", savings_usd=0.03, cost_usd=0.07),
            # No routing telemetry at all — counted in turns_total, not in savings.
            _turn("2026-09-01", routed=None, requested=None, savings_usd=None, cost_usd=0.10),
        ],
    )

    report = build_savings_report(tmp_path)

    assert report.turns_total == 3
    assert report.turns_routed == 2
    assert report.routing_savings_usd == 0.05
    assert report.actual_cost_usd == 0.15
    assert report.top_tier_cost_usd == 0.20
    assert report.savings_pct == 25.0


def test_route_pairs_are_grouped_and_sorted_by_savings(tmp_path: Path) -> None:
    _write_log(
        tmp_path,
        "2026-09-01",
        [
            _turn("2026-09-01", routed="glm-5.2", savings_usd=0.01),
            _turn("2026-09-01", routed="glm-5.2", savings_usd=0.02),
            _turn("2026-09-01", routed="deepseek-v4-flash", savings_usd=0.09),
        ],
    )

    report = build_savings_report(tmp_path)

    assert [(r.routed_model, r.turns) for r in report.by_route] == [
        ("deepseek-v4-flash", 1),
        ("glm-5.2", 2),
    ]
    assert report.by_route[0].savings_usd == 0.09
    assert report.by_route[1].savings_usd == 0.03
    assert report.by_route[0].requested_model == "gpt-5.6-luna"


def test_days_are_grouped_in_calendar_order(tmp_path: Path) -> None:
    _write_log(tmp_path, "2026-08-31", [_turn("2026-08-31", savings_usd=0.04)])
    _write_log(
        tmp_path,
        "2026-09-01",
        [
            _turn("2026-09-01", savings_usd=0.01),
            _turn("2026-09-01", savings_usd=0.02),
        ],
    )

    report = build_savings_report(tmp_path)

    assert [(d.date, d.turns, d.savings_usd) for d in report.by_day] == [
        ("2026-08-31", 1, 0.04),
        ("2026-09-01", 2, 0.03),
    ]
    assert report.start_date == "2026-08-31"
    assert report.end_date == "2026-09-01"


def test_date_filter_excludes_turns_outside_the_window(tmp_path: Path) -> None:
    _write_log(tmp_path, "2026-08-30", [_turn("2026-08-30", savings_usd=0.50)])
    _write_log(tmp_path, "2026-08-31", [_turn("2026-08-31", savings_usd=0.04)])
    _write_log(tmp_path, "2026-09-01", [_turn("2026-09-01", savings_usd=0.01)])

    report = build_savings_report(tmp_path, start_date="2026-08-31", end_date="2026-08-31")

    assert report.turns_total == 1
    assert report.routing_savings_usd == 0.04


def test_turns_split_by_whether_the_router_moved_off_the_requested_model(
    tmp_path: Path,
) -> None:
    _write_log(
        tmp_path,
        "2026-09-01",
        [
            _turn("2026-09-01", routed="glm-5.2", savings_usd=0.02),
            _turn("2026-09-01", routed="deepseek-v4-flash", savings_usd=0.05),
            # Routed off the request, but onto the priciest tier: nothing saved.
            _turn("2026-09-01", routed="gpt-5.6-terra-pro", savings_usd=0.0),
            _turn("2026-09-01", routed="gpt-5.6-luna", savings_usd=0.0),
        ],
    )

    report = build_savings_report(tmp_path)

    assert report.turns_rerouted == 3
    assert report.turns_kept == 1
    assert report.turns_at_top_tier == 2


def test_average_confidence_covers_only_scored_turns(tmp_path: Path) -> None:
    _write_log(
        tmp_path,
        "2026-09-01",
        [
            _turn("2026-09-01", confidence=0.4),
            _turn("2026-09-01", confidence=0.6),
            _turn("2026-09-01", confidence=None),
        ],
    )

    report = build_savings_report(tmp_path)

    assert report.avg_confidence == 0.5


def test_empty_log_dir_yields_a_zeroed_report(tmp_path: Path) -> None:
    report = build_savings_report(tmp_path)

    assert report.turns_total == 0
    assert report.routing_savings_usd == 0.0
    assert report.savings_pct == 0.0
    assert report.by_route == []
    assert report.by_day == []
    assert report.start_date is None
