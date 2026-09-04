#!/usr/bin/env python3
"""Allocation drift + concentration report for gmgn-portfolio.

Reads live `gmgn-cli portfolio holdings --raw` output (or a holdings JSON
file / stdin fixture) and compares current USD weights to an optional target
mix. Does not place trades.

Usage:
  python rebalance.py --wallet <addr> --chain sol --targets "SOL:50,USDC:30,BONK:20"
  python rebalance.py --holdings-file holdings.json --targets "SOL:50,USDC:50"
  gmgn-cli portfolio holdings --chain sol --wallet <addr> --raw | \
    python rebalance.py --targets equal
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

KNOWN_CHAINS = ("sol", "bsc", "base", "eth", "robinhood", "arc", "stable")


@dataclass(frozen=True)
class Holding:
    symbol: str
    address: str
    usd_value: float
    balance: float


@dataclass(frozen=True)
class Row:
    symbol: str
    address: str
    usd_value: float
    current_pct: float
    target_pct: float
    drift_pct: float
    action: str
    trade_usd: float


def _as_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_targets(raw: str) -> dict[str, float]:
    """Parse ``SOL:50,USDC:30`` or the keyword ``equal``.

    Weights are percentages. They must be positive and sum to 100 (±0.01).
    ``equal`` is a sentinel handled by the caller after holdings are known.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("targets string is empty")
    if text.lower() == "equal":
        return {}
    targets: dict[str, float] = {}
    for part in text.split(","):
        piece = part.strip()
        if not piece:
            continue
        if ":" not in piece:
            raise ValueError(f"target {piece!r} must look like SYMBOL:percent")
        symbol, weight = piece.split(":", 1)
        name = symbol.strip().upper()
        if not name:
            raise ValueError(f"target {piece!r} is missing a symbol")
        pct = _as_float(weight)
        if pct <= 0:
            raise ValueError(f"target weight for {name} must be > 0")
        if name in targets:
            raise ValueError(f"duplicate target symbol {name}")
        targets[name] = pct
    if not targets:
        raise ValueError("no targets parsed")
    total = sum(targets.values())
    if abs(total - 100.0) > 0.01:
        raise ValueError(f"target weights must sum to 100, got {total}")
    return targets


def extract_holdings(payload: Any) -> list[Holding]:
    """Normalize gmgn-cli holdings JSON into (symbol, address, usd_value)."""
    if isinstance(payload, dict):
        items = payload.get("holdings")
        if items is None:
            items = payload.get("data")
        if isinstance(items, dict):
            items = items.get("holdings") or items.get("list") or items.get("tokens")
        if items is None:
            items = payload.get("list") or payload.get("tokens")
    elif isinstance(payload, list):
        items = payload
    else:
        items = None
    if not isinstance(items, list):
        raise ValueError("holdings JSON has no holdings array")

    out: list[Holding] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        token = item.get("token") if isinstance(item.get("token"), dict) else {}
        symbol = str(
            token.get("symbol")
            or item.get("symbol")
            or token.get("name")
            or item.get("name")
            or "?"
        ).upper()
        address = str(
            token.get("address")
            or item.get("token_address")
            or item.get("address")
            or ""
        )
        usd_value = _as_float(
            item.get("usd_value")
            if item.get("usd_value") is not None
            else item.get("value_usd")
        )
        balance = _as_float(item.get("balance"))
        if usd_value <= 0:
            continue
        out.append(
            Holding(
                symbol=symbol,
                address=address,
                usd_value=usd_value,
                balance=balance,
            )
        )
    return out


def resolve_targets(holdings: list[Holding], targets: dict[str, float]) -> dict[str, float]:
    if targets:
        return targets
    if not holdings:
        raise ValueError("cannot build an equal-weight mix from an empty portfolio")
    share = 100.0 / len(holdings)
    return {h.symbol: share for h in holdings}


def compute_rows(
    holdings: list[Holding],
    targets: dict[str, float],
    *,
    tolerance: float,
) -> tuple[float, list[Row]]:
    grouped: dict[str, Holding] = {}
    for holding in holdings:
        existing = grouped.get(holding.symbol)
        if existing is None:
            grouped[holding.symbol] = holding
        else:
            grouped[holding.symbol] = Holding(
                symbol=holding.symbol,
                address=existing.address or holding.address,
                usd_value=existing.usd_value + holding.usd_value,
                balance=existing.balance + holding.balance,
            )

    total = sum(h.usd_value for h in grouped.values())
    symbols = list(dict.fromkeys([*targets.keys(), *grouped.keys()]))
    rows: list[Row] = []
    for symbol in symbols:
        holding = grouped.get(symbol)
        usd_value = holding.usd_value if holding else 0.0
        address = holding.address if holding else ""
        current_pct = (usd_value / total * 100.0) if total > 0 else 0.0
        target_pct = targets.get(symbol, 0.0)
        drift_pct = current_pct - target_pct
        trade_usd = (target_pct - current_pct) / 100.0 * total
        if target_pct == 0.0 and usd_value > 0:
            action = f"SELL ${usd_value:,.2f} (unspecified / remainder)"
        elif abs(drift_pct) <= tolerance:
            action = "HOLD"
        elif trade_usd > 0:
            action = f"BUY ${trade_usd:,.2f}"
        else:
            action = f"SELL ${abs(trade_usd):,.2f}"
        rows.append(
            Row(
                symbol=symbol,
                address=address,
                usd_value=usd_value,
                current_pct=current_pct,
                target_pct=target_pct,
                drift_pct=drift_pct,
                action=action,
                trade_usd=trade_usd,
            )
        )
    rows.sort(key=lambda row: row.usd_value, reverse=True)
    return total, rows


def concentration(rows: list[Row]) -> tuple[float, float, str]:
    if not rows:
        return 0.0, 0.0, "?"
    ordered = sorted(rows, key=lambda row: row.current_pct, reverse=True)
    top1 = ordered[0].current_pct
    top3 = sum(row.current_pct for row in ordered[:3])
    if top1 >= 50 or top3 >= 80:
        flag = "HIGH"
    elif top1 >= 30 or top3 >= 60:
        flag = "MED"
    else:
        flag = "LOW"
    return top1, top3, flag


def render_report(
    *,
    wallet: str,
    chain: str,
    total: float,
    rows: list[Row],
    tolerance: float,
) -> str:
    top1, top3, flag = concentration(rows)
    lines = [
        f"Wallet: {wallet or '(fixture)'} | Chain: {chain or '?'}",
        f"Total value: ${total:,.2f}",
        f"Tolerance: ±{tolerance:.2f} pp",
        f"Concentration: top1 {top1:.2f}% · top3 {top3:.2f}% [{flag}]",
        "",
        f"{'Token':<12} {'USD':>12} {'Now%':>8} {'Tgt%':>8} {'Drift':>8}  Action",
        "-" * 72,
    ]
    for row in rows:
        lines.append(
            f"{row.symbol:<12} {row.usd_value:>12,.2f} {row.current_pct:>7.2f} "
            f"{row.target_pct:>7.2f} {row.drift_pct:>+7.2f}  {row.action}"
        )
    lines.append("")
    lines.append("Report only. This script does not place orders.")
    return "\n".join(lines) + "\n"


def load_payload(args: argparse.Namespace) -> Any:
    if args.holdings_file:
        path = args.holdings_file
        if path == "-":
            return json.load(sys.stdin)
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    if args.wallet:
        cmd = [
            "gmgn-cli",
            "portfolio",
            "holdings",
            "--chain",
            args.chain,
            "--wallet",
            args.wallet,
            "--limit",
            str(args.limit),
            "--order-by",
            "usd_value",
            "--direction",
            "desc",
            "--raw",
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=45, check=False
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "gmgn-cli holdings failed").strip()
            raise RuntimeError(message)
        return json.loads(result.stdout)
    if not sys.stdin.isatty():
        return json.load(sys.stdin)
    raise ValueError("pass --wallet, --holdings-file, or pipe holdings JSON on stdin")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GMGN portfolio allocation drift report")
    parser.add_argument("--wallet", default="", help="wallet address (live gmgn-cli path)")
    parser.add_argument("--chain", default="sol", choices=KNOWN_CHAINS)
    parser.add_argument(
        "--targets",
        default="equal",
        help='e.g. "SOL:50,USDC:30,BONK:20" or "equal" for equal-weight baseline',
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=5.0,
        help="hold band in percentage points",
    )
    parser.add_argument(
        "--limit", type=int, default=50, help="holdings page size for live fetch"
    )
    parser.add_argument(
        "--holdings-file", default="", help="JSON fixture or '-' for stdin"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        targets = parse_targets(args.targets)
        payload = load_payload(args)
        holdings = extract_holdings(payload)
        resolved = resolve_targets(holdings, targets)
        total, rows = compute_rows(holdings, resolved, tolerance=args.tolerance)
    except (ValueError, RuntimeError, json.JSONDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(
        render_report(
            wallet=args.wallet,
            chain=args.chain,
            total=total,
            rows=rows,
            tolerance=args.tolerance,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
