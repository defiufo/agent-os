"""Offline regression tests for the gmgn-wallet-score script."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT / "src" / "agentos" / "skills" / "bundled" / "gmgn-wallet-score" / "scripts" / "score.py"
)


def test_score_script_without_args_exits_with_code_2_and_usage() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert "<wallet>" in result.stderr
    assert "<chain>" in result.stderr


def test_score_script_with_single_arg_exits_with_code_2() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "0x123"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert "<wallet>" in result.stderr
    assert "<chain>" in result.stderr


def test_score_script_help_flag_exits_with_code_0() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Usage:" in result.stdout
