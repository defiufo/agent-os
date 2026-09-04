"""`agentos upgrade` command — delegate, check, dry-run, restart+verify."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from agentos.cli import upgrade_cmd
from agentos.cli.install_method import InstallMethod, UpgradePlan

runner = CliRunner()

# A fake checkout root. Assert against SOURCE_DIR_TEXT, never the literal, so the
# expectation matches what the command actually prints: Path renders this as
# "\w\agent-os" on Windows and "/w/agent-os" elsewhere.
SOURCE_DIR = Path("/w/agent-os")
SOURCE_DIR_TEXT = str(SOURCE_DIR)


@pytest.fixture(autouse=True)
def _no_source_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """Quarantine the PEP 610 probe from the developer's own machine.

    A maintainer's checkout really is installed from a directory, so without
    this the source-install notice would fire in every test and its text would
    embed a machine-dependent path. Tests that want the notice opt in.
    """

    monkeypatch.setattr(upgrade_cmd, "installed_from_directory", lambda *a, **k: None)


def _app() -> typer.Typer:
    app = typer.Typer()
    app.command("upgrade")(upgrade_cmd.upgrade_command)

    @app.command("noop")
    def _noop() -> None:  # keeps Typer in multi-command mode
        return None

    return app


def _delegated_plan() -> UpgradePlan:
    return UpgradePlan(
        method=InstallMethod.UV_TOOL,
        delegated=True,
        tool="/abs/uv",
        command=[
            "/abs/uv",
            "tool",
            "install",
            "--force",
            "--python",
            "3.12",
            "use-agent-os[recommended]",
        ],
        manual_hint='uv tool install --force --python 3.12 "use-agent-os[recommended]"',
    )


def _pip_plan() -> UpgradePlan:
    return UpgradePlan(
        method=InstallMethod.PIP,
        delegated=False,
        tool=None,
        command=["python", "-m", "pip", "install", "--upgrade", "use-agent-os"],
        manual_hint="python -m pip install --upgrade use-agent-os",
    )


def _ok_run(*_: Any, **__: Any) -> upgrade_cmd.UpgradeRunResult:
    return upgrade_cmd.UpgradeRunResult(
        ok=True, timed_out=False, returncode=0, stdout="upgraded", stderr=""
    )


def _json_payload(stdout: str) -> dict[str, Any]:
    """The `--json` object, which progress prose may precede on stdout."""

    start = stdout.index("{")
    payload = json.loads(stdout[start:])
    assert isinstance(payload, dict)
    return payload


# --- --check ---------------------------------------------------------------


def test_check_reports_newer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upgrade_cmd, "build_upgrade_plan", _delegated_plan)
    monkeypatch.setattr(
        "agentos.compat.pypi_client.latest_version", lambda timeout=5.0: "99999.1.1"
    )
    result = runner.invoke(_app(), ["upgrade", "--check"])
    assert result.exit_code == 0
    assert "newer version is available" in result.stdout


def test_check_offline_exit_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upgrade_cmd, "build_upgrade_plan", _delegated_plan)
    monkeypatch.setattr("agentos.compat.pypi_client.latest_version", lambda timeout=5.0: None)
    result = runner.invoke(_app(), ["upgrade", "--check"])
    assert result.exit_code == 0
    assert "could not check (offline)" in result.stdout


def test_check_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"run": False}
    monkeypatch.setattr(upgrade_cmd, "build_upgrade_plan", _delegated_plan)
    monkeypatch.setattr(
        "agentos.compat.pypi_client.latest_version", lambda timeout=5.0: "99999.1.1"
    )
    monkeypatch.setattr(
        upgrade_cmd,
        "_run_upgrade_subprocess",
        lambda *a, **k: called.__setitem__("run", True),
    )
    runner.invoke(_app(), ["upgrade", "--check"])
    assert called["run"] is False


# --- non-delegated (pip/editable) ------------------------------------------


def test_pip_prints_manual_and_exits_3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upgrade_cmd, "build_upgrade_plan", _pip_plan)
    result = runner.invoke(_app(), ["upgrade"])
    assert result.exit_code == 3
    assert "pip install --upgrade use-agent-os" in result.stdout


# --- --dry-run -------------------------------------------------------------


def test_dry_run_touches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    ran = {"run": False}
    monkeypatch.setattr(upgrade_cmd, "build_upgrade_plan", _delegated_plan)
    monkeypatch.setattr(
        upgrade_cmd, "_run_upgrade_subprocess", lambda *a, **k: ran.__setitem__("run", True)
    )
    result = runner.invoke(_app(), ["upgrade", "--dry-run"])
    assert result.exit_code == 0
    assert (
        "Would run: /abs/uv tool install --force --python 3.12 use-agent-os[recommended]"
        in result.stdout
    )
    assert ran["run"] is False


# --- source-install notice (PEP 610 directory install) ---------------------


def test_source_install_notice_names_the_way_back(monkeypatch: pytest.MonkeyPatch) -> None:
    # A checkout-backed install is exactly the case this command replaces, so
    # it must say so and name install_source.sh — but never block.
    monkeypatch.setattr(upgrade_cmd, "build_upgrade_plan", _delegated_plan)
    monkeypatch.setattr(upgrade_cmd, "installed_from_directory", lambda *a, **k: SOURCE_DIR)
    monkeypatch.setattr(upgrade_cmd, "_run_upgrade_subprocess", _ok_run)
    monkeypatch.setattr(upgrade_cmd, "_installed_version_via", lambda *a, **k: "9.9.9")
    monkeypatch.setattr(upgrade_cmd, "_restart_and_verify", lambda **k: True)

    result = runner.invoke(_app(), ["upgrade"])
    assert result.exit_code == 0
    assert SOURCE_DIR_TEXT in result.stdout
    assert "scripts/install_source.sh" in result.stdout
    assert "Upgraded" in result.stdout


def test_no_source_install_notice_for_a_release_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(upgrade_cmd, "build_upgrade_plan", _delegated_plan)
    monkeypatch.setattr(upgrade_cmd, "_run_upgrade_subprocess", _ok_run)
    monkeypatch.setattr(upgrade_cmd, "_installed_version_via", lambda *a, **k: "9.9.9")
    monkeypatch.setattr(upgrade_cmd, "_restart_and_verify", lambda **k: True)

    result = runner.invoke(_app(), ["upgrade"])
    assert result.exit_code == 0
    assert "install_source.sh" not in result.stdout


def test_dry_run_reports_the_source_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    ran = {"run": False}
    monkeypatch.setattr(upgrade_cmd, "build_upgrade_plan", _delegated_plan)
    monkeypatch.setattr(upgrade_cmd, "installed_from_directory", lambda *a, **k: SOURCE_DIR)
    monkeypatch.setattr(
        upgrade_cmd, "_run_upgrade_subprocess", lambda *a, **k: ran.__setitem__("run", True)
    )

    result = runner.invoke(_app(), ["upgrade", "--dry-run", "--json"])
    assert result.exit_code == 0
    assert _json_payload(result.stdout)["sourceDirectory"] == SOURCE_DIR_TEXT
    assert ran["run"] is False


def test_success_json_reports_the_source_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upgrade_cmd, "build_upgrade_plan", _delegated_plan)
    monkeypatch.setattr(upgrade_cmd, "installed_from_directory", lambda *a, **k: SOURCE_DIR)
    monkeypatch.setattr(upgrade_cmd, "_run_upgrade_subprocess", _ok_run)
    monkeypatch.setattr(upgrade_cmd, "_installed_version_via", lambda *a, **k: "9.9.9")
    monkeypatch.setattr(upgrade_cmd, "_restart_and_verify", lambda **k: True)

    result = runner.invoke(_app(), ["upgrade", "--json"])
    assert result.exit_code == 0
    payload = _json_payload(result.stdout)
    assert payload["sourceDirectory"] == SOURCE_DIR_TEXT
    assert payload["new"] == "9.9.9"


def test_release_install_json_reports_null_source_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(upgrade_cmd, "build_upgrade_plan", _delegated_plan)
    monkeypatch.setattr(upgrade_cmd, "_run_upgrade_subprocess", _ok_run)
    monkeypatch.setattr(upgrade_cmd, "_installed_version_via", lambda *a, **k: "9.9.9")
    monkeypatch.setattr(upgrade_cmd, "_restart_and_verify", lambda **k: True)

    result = runner.invoke(_app(), ["upgrade", "--json"])
    assert result.exit_code == 0
    assert _json_payload(result.stdout)["sourceDirectory"] is None


# --- successful delegate + restart+verify ----------------------------------


def test_upgrade_success_restarts_and_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upgrade_cmd, "build_upgrade_plan", _delegated_plan)
    monkeypatch.setattr(upgrade_cmd, "_run_upgrade_subprocess", _ok_run)
    monkeypatch.setattr(upgrade_cmd, "_installed_version_via", lambda *a, **k: "99999.2.0")
    seen: dict[str, Any] = {}

    def fake_restart(**kwargs: Any) -> bool:
        seen.update(kwargs)
        return True

    monkeypatch.setattr(upgrade_cmd, "_restart_and_verify", fake_restart)
    result = runner.invoke(_app(), ["upgrade"])
    assert result.exit_code == 0
    assert "Upgraded:" in result.stdout
    assert "→ 99999.2.0" in result.stdout
    assert seen["expected_version"] == "99999.2.0"


def test_upgrade_verify_failure_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upgrade_cmd, "build_upgrade_plan", _delegated_plan)
    monkeypatch.setattr(upgrade_cmd, "_run_upgrade_subprocess", _ok_run)
    monkeypatch.setattr(upgrade_cmd, "_installed_version_via", lambda *a, **k: "99999.2.0")
    monkeypatch.setattr(upgrade_cmd, "_restart_and_verify", lambda **k: False)
    result = runner.invoke(_app(), ["upgrade"])
    assert result.exit_code == 1


# --- --no-restart ----------------------------------------------------------


def test_no_restart_loud_warning_exit_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upgrade_cmd, "build_upgrade_plan", _delegated_plan)
    monkeypatch.setattr(upgrade_cmd, "_run_upgrade_subprocess", _ok_run)
    monkeypatch.setattr(upgrade_cmd, "_installed_version_via", lambda *a, **k: "99999.2.0")
    restarted = {"called": False}
    monkeypatch.setattr(
        upgrade_cmd,
        "_restart_and_verify",
        lambda **k: restarted.__setitem__("called", True),
    )
    result = runner.invoke(_app(), ["upgrade", "--no-restart"])
    assert result.exit_code == 0
    assert restarted["called"] is False
    # Loud warning, prefixed ⚠ (emitted to stderr; CliRunner merges streams).
    assert "⚠" in result.output
    assert "OLD version" in result.output


# --- timeout ---------------------------------------------------------------


def test_upgrade_timeout_exits_one_with_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upgrade_cmd, "build_upgrade_plan", _delegated_plan)
    monkeypatch.setattr(
        upgrade_cmd,
        "_run_upgrade_subprocess",
        lambda *a, **k: upgrade_cmd.UpgradeRunResult(
            ok=False, timed_out=True, returncode=None, stdout="", stderr=""
        ),
    )
    result = runner.invoke(_app(), ["upgrade"])
    assert result.exit_code == 1
    assert "timed out" in result.stdout
    assert "process group" in result.stdout


def test_upgrade_failure_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upgrade_cmd, "build_upgrade_plan", _delegated_plan)
    monkeypatch.setattr(
        upgrade_cmd,
        "_run_upgrade_subprocess",
        lambda *a, **k: upgrade_cmd.UpgradeRunResult(
            ok=False, timed_out=False, returncode=2, stdout="", stderr="boom"
        ),
    )
    result = runner.invoke(_app(), ["upgrade"])
    assert result.exit_code == 1
    assert "Upgrade failed" in result.stdout


# --- _kill_process_group ---------------------------------------------------


class _FakeProc:
    """Minimal subprocess.Popen stand-in: records kills, never exits."""

    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.killed = False

    def kill(self) -> None:
        self.killed = True

    def poll(self) -> None:
        return None


def test_kill_process_group_windows_kills_whole_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows the timeout kill must terminate the ENTIRE process tree.

    Regression for #536: ``proc.kill()`` (``TerminateProcess``) only kills the
    direct child, orphaning grandchildren (compilers, downloads) that hold
    file locks on the virtualenv. ``taskkill /T /F`` terminates the tree.
    """

    monkeypatch.setattr(upgrade_cmd.os, "name", "nt")
    calls: list[tuple[Any, Any]] = []

    def fake_run(*args: Any, **kwargs: Any) -> types.SimpleNamespace:
        calls.append((args, kwargs))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(upgrade_cmd.subprocess, "run", fake_run)
    proc = _FakeProc(4242)
    upgrade_cmd._kill_process_group(proc)

    assert calls, "taskkill must be invoked on Windows"
    argv = calls[0][0][0]
    assert argv[:2] == ["taskkill", "/T"], "must kill the whole tree with /T"
    assert "/F" in argv
    assert argv[argv.index("/PID") + 1] == "4242"
    assert calls[0][1]["timeout"] == 5
    assert proc.killed is False, "fallback kill() must not fire when taskkill succeeds"


def test_kill_process_group_windows_falls_back_when_taskkill_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If taskkill itself errors (timeout/OSError), degrade to proc.kill()."""

    monkeypatch.setattr(upgrade_cmd.os, "name", "nt")

    def fake_run(*args: Any, **kwargs: Any) -> types.SimpleNamespace:
        raise subprocess.TimeoutExpired(cmd="taskkill", timeout=5)

    monkeypatch.setattr(upgrade_cmd.subprocess, "run", fake_run)
    proc = _FakeProc(4242)
    upgrade_cmd._kill_process_group(proc)
    assert proc.killed is True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only code path (os.getpgid/killpg)")
def test_kill_process_group_unix_still_uses_killpg(monkeypatch: pytest.MonkeyPatch) -> None:
    """POSIX behavior is unchanged: SIGTERM then SIGKILL to the process group."""

    monkeypatch.setattr(upgrade_cmd.os, "name", "posix")
    monkeypatch.setattr(upgrade_cmd.os, "getpgid", lambda pid: 9999)
    sent: list[tuple[int, Any]] = []
    monkeypatch.setattr(upgrade_cmd.os, "killpg", lambda pgid, sig: sent.append((pgid, sig)))
    proc = _FakeProc(4242)
    # First SIGTERM lands; fake proc dies instantly so the loop exits before SIGKILL.
    proc.poll = lambda: 0
    upgrade_cmd._kill_process_group(proc)
    assert sent == [(9999, signal.SIGTERM)]


# --- _run_upgrade_subprocess timeout path ----------------------------------


class _StuckPopen:
    """Popen stand-in whose communicate() always times out.

    Simulates the #536 regression: after the tree kill, orphaned grandchildren
    keep the inherited stdout/stderr pipe handles open, so communicate() never
    returns. The CLI must still give up instead of hanging.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.pid = 4242
        self.returncode: int | None = None

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        raise subprocess.TimeoutExpired(cmd="uv", timeout=timeout or 0)


def test_upgrade_subprocess_windows_guards_stuck_communicate_after_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stuck pipe handle after the kill must not hang the CLI (Windows path)."""

    monkeypatch.setattr(upgrade_cmd.os, "name", "nt")
    monkeypatch.setattr(upgrade_cmd.subprocess, "Popen", _StuckPopen)
    killed: list[Any] = []
    monkeypatch.setattr(upgrade_cmd, "_kill_process_group", lambda p: killed.append(p))

    result = upgrade_cmd._run_upgrade_subprocess(
        ["uv", "tool", "install", "use-agent-os"], env={}, timeout=1.0
    )

    assert killed, "_kill_process_group must run on timeout"
    assert result.timed_out is True
    assert result.ok is False
    assert result.stdout == ""
    assert result.stderr == ""
