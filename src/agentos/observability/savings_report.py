"""Aggregate Pilot Router cost savings out of the decision log.

Every completed turn writes a :class:`~agentos.observability.decision_log.
SavingsTelemetry` block to ``~/.agentos/logs/decisions-YYYYMMDD.jsonl``. This
module rolls the routing part of those blocks up into a :class:`SavingsReport`
— the shape rendered by ``agentos cost savings`` as a table, as JSON/CSV, and
as the branded PDF.

What the dollar figure actually means
-------------------------------------
The engine computes per-turn routing savings in ``runtime.
_compute_route_input_savings_usd`` as::

    (top_tier_input_price_per_m - routed_input_price_per_m) * input_tokens / 1e6

So the comparison is against **the most expensive model configured in
``[router.tiers]``** — the bill you would have run up sending every turn to
your top tier — and not against the model named by the telemetry's
``baseline_model`` field, which records the model the *request* arrived with.
This module therefore reports that field as ``requested_model`` and never
implies it is the price comparison.

Two consequences worth carrying into any presentation of these numbers:

* Only **input** tokens are priced. Output tokens, which are dearer per token
  on every tier, are not counted — the figure is a conservative floor.
* The per-turn value is clamped at zero, so a turn routed onto the top tier
  contributes nothing rather than a negative.

**Scope:** routing only. The other savings mechanisms in the telemetry block
(tool-result projection, short-reply enforcement, cache hits, thinking mode)
are deliberately excluded so the headline number is attributable to the router
and nothing else.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agentos.observability.decision_log import DecisionEntry, load_entries

#: Monetary aggregates are rounded to this many decimals. Per-turn savings run
#: to fractions of a cent, so summing raw floats leaves visible drift.
_USD_PRECISION = 6
_PCT_PRECISION = 2
_CONFIDENCE_PRECISION = 4


@dataclass
class RouteRow:
    """One ``requested_model -> routed_model`` pair, summed over the window."""

    requested_model: str
    routed_model: str
    turns: int
    savings_usd: float
    avg_savings_pct: float | None
    avg_confidence: float | None


@dataclass
class DayRow:
    """One calendar day (UTC), summed over the turns the router decided."""

    date: str
    turns: int
    savings_usd: float
    actual_cost_usd: float


@dataclass
class SavingsReport:
    """Pilot Router savings over a window of decision-log turns.

    ``actual_cost_usd`` and ``top_tier_cost_usd`` cover only the turns the
    router actually decided (``turns_routed``), so the two are comparable:
    ``savings_pct`` is the share of the top-tier bill that routing avoided.

    ``top_tier_cost_usd`` is ``actual_cost_usd`` plus the input-side savings.
    Because the engine prices only input tokens, this understates what the top
    tier would really have charged — its output tokens are dearer too.
    """

    start_date: str | None
    end_date: str | None
    turns_total: int
    turns_routed: int
    #: Turns the router moved off the model the request arrived with.
    turns_rerouted: int
    #: Turns the router landed back on the requested model.
    turns_kept: int
    #: Turns that saved nothing because they ran on the most expensive tier.
    turns_at_top_tier: int
    actual_cost_usd: float
    routing_savings_usd: float
    top_tier_cost_usd: float
    savings_pct: float
    avg_confidence: float | None
    tokens_input: int
    tokens_output: int
    by_route: list[RouteRow] = field(default_factory=list)
    by_day: list[DayRow] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return the report as plain JSON-serialisable data."""

        return asdict(self)


def _entry_date(entry: DecisionEntry) -> str:
    """Return the ``YYYY-MM-DD`` day of a turn, from its ISO timestamp."""

    return entry.ts[:10]


def collect_entries(
    log_dir: Path,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[DecisionEntry]:
    """Load every decision-log turn in ``log_dir`` inside the date window.

    Dates are inclusive ``YYYY-MM-DD`` strings compared against the turn's own
    UTC timestamp, not the log filename — a turn just after midnight UTC lands
    in the day it was actually served.
    """

    if not log_dir.is_dir():
        return []

    entries: list[DecisionEntry] = []
    for path in sorted(log_dir.glob("decisions-*.jsonl")):
        for entry in load_entries(path):
            day = _entry_date(entry)
            if start_date and day < start_date:
                continue
            if end_date and day > end_date:
                continue
            entries.append(entry)
    entries.sort(key=lambda e: e.ts)
    return entries


def build_savings_report(
    log_dir: Path,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> SavingsReport:
    """Aggregate Pilot Router savings from the decision logs in ``log_dir``."""

    entries = collect_entries(log_dir, start_date=start_date, end_date=end_date)

    routed = 0
    rerouted = 0
    kept = 0
    at_top_tier = 0
    savings_usd = 0.0
    actual_cost = 0.0
    tokens_input = 0
    tokens_output = 0
    confidences: list[float] = []

    route_turns: dict[tuple[str, str], int] = defaultdict(int)
    route_savings: dict[tuple[str, str], float] = defaultdict(float)
    route_pcts: dict[tuple[str, str], list[float]] = defaultdict(list)
    route_confidences: dict[tuple[str, str], list[float]] = defaultdict(list)

    day_turns: dict[str, int] = defaultdict(int)
    day_savings: dict[str, float] = defaultdict(float)
    day_cost: dict[str, float] = defaultdict(float)

    for entry in entries:
        telemetry = entry.savings
        delta = telemetry.routing_savings_usd_estimated_vs_baseline
        requested = telemetry.baseline_model
        if delta is None or not telemetry.routed_model or not requested:
            continue

        routed += 1
        savings_usd += delta
        actual_cost += telemetry.cost_usd or telemetry.billed_cost_usd or 0.0
        tokens_input += entry.tokens_input
        tokens_output += entry.tokens_output

        if telemetry.routed_model == requested:
            kept += 1
        else:
            rerouted += 1
        if delta == 0:
            at_top_tier += 1

        if telemetry.routing_confidence is not None:
            confidences.append(telemetry.routing_confidence)

        pair = (requested, telemetry.routed_model)
        route_turns[pair] += 1
        route_savings[pair] += delta
        if telemetry.routing_savings_pct is not None:
            route_pcts[pair].append(telemetry.routing_savings_pct)
        if telemetry.routing_confidence is not None:
            route_confidences[pair].append(telemetry.routing_confidence)

        day = _entry_date(entry)
        day_turns[day] += 1
        day_savings[day] += delta
        day_cost[day] += telemetry.cost_usd or telemetry.billed_cost_usd or 0.0

    by_route = sorted(
        (
            RouteRow(
                requested_model=requested_model,
                routed_model=model,
                turns=route_turns[(requested_model, model)],
                savings_usd=round(route_savings[(requested_model, model)], _USD_PRECISION),
                avg_savings_pct=_mean(route_pcts[(requested_model, model)], _PCT_PRECISION),
                avg_confidence=_mean(
                    route_confidences[(requested_model, model)], _CONFIDENCE_PRECISION
                ),
            )
            for requested_model, model in route_turns
        ),
        key=lambda row: (-row.savings_usd, row.routed_model),
    )

    by_day = [
        DayRow(
            date=day,
            turns=day_turns[day],
            savings_usd=round(day_savings[day], _USD_PRECISION),
            actual_cost_usd=round(day_cost[day], _USD_PRECISION),
        )
        for day in sorted(day_turns)
    ]

    top_tier_cost = actual_cost + savings_usd
    savings_pct = (savings_usd / top_tier_cost * 100.0) if top_tier_cost > 0 else 0.0

    return SavingsReport(
        start_date=by_day[0].date if by_day else None,
        end_date=by_day[-1].date if by_day else None,
        turns_total=len(entries),
        turns_routed=routed,
        turns_rerouted=rerouted,
        turns_kept=kept,
        turns_at_top_tier=at_top_tier,
        actual_cost_usd=round(actual_cost, _USD_PRECISION),
        routing_savings_usd=round(savings_usd, _USD_PRECISION),
        top_tier_cost_usd=round(top_tier_cost, _USD_PRECISION),
        savings_pct=round(savings_pct, _PCT_PRECISION),
        avg_confidence=_mean(confidences, _CONFIDENCE_PRECISION),
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        by_route=by_route,
        by_day=by_day,
    )


def _mean(values: list[float], precision: int) -> float | None:
    """Return the rounded mean of ``values``, or ``None`` when empty."""

    if not values:
        return None
    return round(sum(values) / len(values), precision)
