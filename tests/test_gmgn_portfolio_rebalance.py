"""Offline tests for the gmgn-portfolio allocation-drift helper.

The helper never hits GMGN in this file. Fixtures stand in for
``gmgn-cli portfolio holdings --raw`` so default CI stays credential-free.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "src"
    / "agentos"
    / "skills"
    / "bundled"
    / "gmgn-portfolio"
    / "scripts"
    / "rebalance.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("gmgn_portfolio_rebalance", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()

SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

HOLDINGS = {
    "holdings": [
        {"token": {"symbol": "SOL", "address": SOL}, "usd_value": 50, "balance": 1},
        {"token": {"symbol": "USDC", "address": USDC}, "usd_value": 30, "balance": 30},
        {
            "token": {"symbol": "BONK", "address": BONK},
            "usd_value": 20,
            "balance": 2_000_000,
        },
    ]
}


def test_parse_targets_requires_hundred() -> None:
    parsed = mod.parse_targets("SOL:50,USDC:30,BONK:20")
    assert parsed == {"SOL": 50.0, "USDC": 30.0, "BONK": 20.0}
    with pytest.raises(ValueError, match="sum to 100"):
        mod.parse_targets("SOL:40,USDC:40")
    with pytest.raises(ValueError, match="duplicate"):
        mod.parse_targets("SOL:50,sol:50")
    assert mod.parse_targets("equal") == {}


def test_extract_holdings_skips_zero_value() -> None:
    payload = {
        "holdings": [
            {"token": {"symbol": "SOL", "address": "So1"}, "usd_value": "10.5"},
            {"token": {"symbol": "DUST", "address": "x"}, "usd_value": 0},
        ]
    }
    holdings = mod.extract_holdings(payload)
    assert len(holdings) == 1
    assert holdings[0].symbol == "SOL"
    assert holdings[0].usd_value == 10.5


def test_drift_and_concentration_on_fixture() -> None:
    holdings = mod.extract_holdings(HOLDINGS)
    targets = mod.parse_targets("SOL:40,USDC:40,BONK:20")
    total, rows = mod.compute_rows(holdings, targets, tolerance=5.0)
    assert total == 100.0
    by_symbol = {row.symbol: row for row in rows}
    assert by_symbol["SOL"].current_pct == 50.0
    assert by_symbol["SOL"].drift_pct == 10.0
    assert by_symbol["SOL"].action.startswith("SELL")
    assert by_symbol["USDC"].action.startswith("BUY")
    assert by_symbol["BONK"].action == "HOLD"
    top1, top3, flag = mod.concentration(rows)
    assert top1 == 50.0
    assert top3 == 100.0
    assert flag == "HIGH"


def test_unspecified_holding_is_flagged_as_remainder() -> None:
    holdings = mod.extract_holdings(HOLDINGS)
    targets = mod.parse_targets("SOL:70,USDC:30")
    _total, rows = mod.compute_rows(holdings, targets, tolerance=1.0)
    bonk = next(row for row in rows if row.symbol == "BONK")
    assert bonk.target_pct == 0.0
    assert "remainder" in bonk.action.lower()


def test_equal_weight_baseline() -> None:
    holdings = mod.extract_holdings(HOLDINGS)
    targets = mod.resolve_targets(holdings, {})
    share = pytest.approx(100 / 3)
    assert targets == {"SOL": share, "USDC": share, "BONK": share}


def test_cli_fixture_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "holdings.json"
    path.write_text(json.dumps(HOLDINGS), encoding="utf-8")
    code = mod.main(
        [
            "--holdings-file",
            str(path),
            "--targets",
            "SOL:50,USDC:30,BONK:20",
            "--chain",
            "sol",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "Total value: $100.00" in captured.out
    assert "HOLD" in captured.out
    assert "does not place orders" in captured.out
