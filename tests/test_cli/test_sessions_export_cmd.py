"""Issue #678: ``agentos sessions export`` must not write outside the CWD.

The gateway round-trip is stubbed at ``run_gateway_sync`` — these cover the
default-filename derivation, which is where the user-supplied ``session_id``
reaches the filesystem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentos.cli import sessions_cmd

runner = CliRunner()


class _FakeClient:
    async def resolve_session(self, session_id: str) -> dict[str, Any]:
        return {"session_key": session_id, "status": "done", "model": "gpt-x"}

    async def preview_sessions(self, keys: list[str]) -> dict[str, Any]:
        return {"previews": [{"lastMessage": "hello"}]}

    async def session_history(self, key: str, limit: int = 1000) -> dict[str, Any]:
        return {"messages": []}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    fake = _FakeClient()

    async def _with_client(action):
        return await action(fake)

    monkeypatch.setattr(sessions_cmd, "_with_client", _with_client)
    return fake


@pytest.mark.parametrize(
    ("session_id", "expected"),
    [
        ("agent:main:cli:aaa", "agent-main-cli-aaa"),
        ("../../etc/pwned", "etc-pwned"),
        ("..\\..\\Windows\\pwned", "Windows-pwned"),
        ("/absolute/path", "absolute-path"),
        ("..", "session"),
        ("", "session"),
    ],
)
def test_safe_export_stem_strips_path_separators(
    session_id: str, expected: str
) -> None:
    """_safe_archive_part is reused for export filenames."""
    from agentos.session.manager import _safe_archive_part

    assert _safe_archive_part(session_id) == expected


def test_export_writes_inside_the_working_directory(
    client: _FakeClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A traversal id lands in the CWD under a flattened name, not at /etc."""
    monkeypatch.chdir(tmp_path)
    escaped = tmp_path.parent / "pwned.json"

    result = runner.invoke(sessions_cmd.app, ["export", "../pwned", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert not escaped.exists()
    assert (tmp_path / "pwned.json").exists()


def test_export_honours_an_explicit_output_path(
    client: _FakeClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--output is the caller's own choice and is left untouched."""
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "nested" / "chosen.md"
    target.parent.mkdir()

    result = runner.invoke(
        sessions_cmd.app, ["export", "agent:main:cli:aaa", "--output", str(target)]
    )

    assert result.exit_code == 0
    assert target.exists()
