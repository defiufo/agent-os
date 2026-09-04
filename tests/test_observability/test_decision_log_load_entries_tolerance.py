"""Regression tests for #812: load_entries must skip malformed lines.

The decisions JSONL file is append-only and is written once per turn. A
SIGKILL mid-turn, an OOM, a disk-full error or a malformed ``json.dumps``
call can leave a partial / corrupt line in the file. ``load_entries`` is
the shared reader for every downstream report (cost savings, session
export, pipeline replay) and must not raise on the first bad line.

The pre-existing behaviour was a single ``json.loads(line)`` call inside a
loop with no try/except. PRs #871/#872 added partial coverage (one
exception type, no skip accounting, no companion log entry). The fix in
this PR catches all three of the realistic failure shapes:

* ``json.JSONDecodeError`` — a truncated / corrupted JSONL line
* ``ValueError`` — ``dataclass(...)`` constructor rejecting a wrong type
* ``TypeError`` — ``_filter_payload(...)`` walking a non-dict

Each is logged at debug (single line) and a summary warning is logged at
the end so that production deployments can alert on it. The tests
exercise the same shape the fix uses: assemble a JSONL file with one
healthy and several malformed lines, call ``load_entries``, and assert
that only the healthy line is returned.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import pytest

from agentos.observability.decision_log import (
    DecisionEntry,
    load_entries,
    write_decision_entry,
)


def _good_entry() -> DecisionEntry:
    return DecisionEntry(
        turn_id="turn-good",
        session_key="agent:main:good",
        prompt_hash="abc",
        system_prompt_hash="def",
        tool_list_hash="ghi",
        tool_choice="auto",
        tokens_input=10,
        tokens_output=20,
        model="test-model",
        provider="test-provider",
        latency_ms=100,
        ts="2026-09-02T13:00:00Z",
    )


@pytest.fixture
def frozen_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``datetime.now(UTC)`` so ``write_decision_entry`` lands in a known path."""
    real_dt = _dt.datetime

    class _FrozenDateTime(real_dt):
        @classmethod
        def now(cls, tz: object = None) -> _dt.datetime:
            return real_dt(2026, 9, 2, 13, 0, tzinfo=tz or _dt.UTC)

    monkeypatch.setattr(
        "agentos.observability.decision_log.datetime", _FrozenDateTime
    )


def _today_path(tmp_path: Path) -> Path:
    return tmp_path / "logs" / "decisions-20260902.jsonl"


def test_load_entries_skips_truncated_jsonl_line(
    frozen_today: None, tmp_path: Path
) -> None:
    log_dir = tmp_path / "logs"
    write_decision_entry(_good_entry(), log_dir=log_dir)
    path = _today_path(tmp_path)
    with path.open("a", encoding="utf-8") as fh:
        # SIGKILL mid-write: closes the object opener but loses the rest.
        fh.write('{"turn_id": "turn-broken", "session_ke')
    entries = load_entries(path)
    assert [e.turn_id for e in entries] == ["turn-good"]


def test_load_entries_skips_wrong_shape(
    frozen_today: None, tmp_path: Path
) -> None:
    """A line that parses as JSON but not as a DecisionEntry-shaped payload."""

    log_dir = tmp_path / "logs"
    write_decision_entry(_good_entry(), log_dir=log_dir)
    path = _today_path(tmp_path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(["this", "is", "an", "array"]) + "\n")
        fh.write(json.dumps(12345) + "\n")
        fh.write(json.dumps(None) + "\n")
    entries = load_entries(path)
    assert len(entries) == 1
    assert entries[0].turn_id == "turn-good"


def test_load_entries_skips_when_dataclass_rejects_field_type(
    frozen_today: None, tmp_path: Path
) -> None:
    """``savings`` is expected to be a dict; a string raises ``AttributeError``.

    Python dataclasses do not enforce types on ``__init__`` so the original
    ValueError-class bug only fires when a nested container has the wrong
    shape: ``_filter_payload(SavingsTelemetry, "string")`` calls
    ``"string".items()`` and raises ``AttributeError``. A truncated/partial
    JSON payload with this shape used to crash every reader of the daily
    decisions file.
    """

    log_dir = tmp_path / "logs"
    write_decision_entry(_good_entry(), log_dir=log_dir)
    path = _today_path(tmp_path)
    with path.open("a", encoding="utf-8") as fh:
        bad = {
            "turn_id": "turn-bad-type",
            "session_key": "agent:main:bad",
            "prompt_hash": "abc",
            "system_prompt_hash": "def",
            "tool_list_hash": "ghi",
            "tool_choice": "auto",
            "tokens_input": 10,
            "tokens_output": 20,
            "model": "x",
            "provider": "x",
            "latency_ms": 10,
            "ts": "2026-09-02T13:00:00Z",
            "savings": "this should be a dict, not a string",
        }
        fh.write(json.dumps(bad) + "\n")
    entries = load_entries(path)
    assert len(entries) == 1
    assert entries[0].turn_id == "turn-good"


def test_load_entries_returns_empty_when_file_missing(tmp_path: Path) -> None:
    """Existing behaviour: a missing file is not an error."""
    assert load_entries(tmp_path / "missing.jsonl") == []


def test_load_entries_skips_blank_lines(
    frozen_today: None, tmp_path: Path
) -> None:
    log_dir = tmp_path / "logs"
    write_decision_entry(_good_entry(), log_dir=log_dir)
    path = _today_path(tmp_path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n\n\n")
    entries = load_entries(path)
    assert len(entries) == 1


def test_load_entries_logs_each_skipped_line(
    frozen_today: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Each skipped line is recorded at debug level so production can audit it."""
    seen: list[tuple[str, dict]] = []

    def _capture(event: str, **kw: object) -> None:
        seen.append((event, kw))

    monkeypatch.setattr(
        "agentos.observability.decision_log.log.debug", _capture
    )
    monkeypatch.setattr(
        "agentos.observability.decision_log.log.warning", _capture
    )

    log_dir = tmp_path / "logs"
    write_decision_entry(_good_entry(), log_dir=log_dir)
    path = _today_path(tmp_path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
        fh.write(json.dumps(["array"]) + "\n")
        fh.write(json.dumps({"turn_id": 123, "tokens_input": "wrong"}) + "\n")

    load_entries(path)

    debug_events = [
        e for e in seen if e[0] == "decision_log.skipped_malformed_line"
    ]
    warning_events = [e for e in seen if e[0] == "decision_log.skipped_malformed_lines"]

    assert len(debug_events) == 3
    for _event, kw in debug_events:
        assert kw["path"] == str(path)
        assert "line_no" in kw
        assert "error" in kw

    assert len(warning_events) == 1
    _event, summary_kw = warning_events[0]
    assert summary_kw["skipped"] == 3
    assert summary_kw["loaded"] == 1


def test_load_entries_all_lines_bad_returns_empty(tmp_path: Path) -> None:
    """Every line malformed, every line skipped: the report renders empty."""
    path = tmp_path / "decisions.jsonl"
    path.write_text("{not json\n[1,2]\n}\n", encoding="utf-8")
    assert load_entries(path) == []
