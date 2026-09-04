"""CLI tests for ``agentos config set``."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from agentos.cli.config_cmd import _set_key
from agentos.cli.main import app
from agentos.gateway.config import GatewayConfig
from agentos.onboarding.config_store import load_config
from agentos.skills.config_vars import _value_at

runner = CliRunner()


def test_set_key_creates_nested_skills_config() -> None:
    data = GatewayConfig().to_toml_dict()
    assert "config" not in data.get("skills", {})
    assert _set_key(data, "skills.config.wiki.path", "/srv/wiki") is True
    assert data["skills"]["config"]["wiki"]["path"] == "/srv/wiki"
    cfg = GatewayConfig.model_validate(data)
    assert _value_at(cfg, "wiki.path") == "/srv/wiki"


def test_set_key_rejects_unknown_keys_outside_skills_config() -> None:
    data = GatewayConfig().to_toml_dict()
    assert _set_key(data, "skills.no_such_key", "x") is False
    assert _set_key(data, "skills.max_skills_prompt_chars", 32000) is True


def test_config_set_persists_documented_wiki_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTOS_STATE_DIR", str(tmp_path / "state"))
    cfg_path = tmp_path / "config.toml"
    result = runner.invoke(
        app,
        [
            "config",
            "set",
            "skills.config.wiki.path",
            "/srv/wiki",
            "--config",
            str(cfg_path),
        ],
    )
    assert result.exit_code == 0, result.output
    loaded = load_config(cfg_path)
    assert _value_at(loaded, "wiki.path") == "/srv/wiki"


def test_config_set_existing_model_field_still_works(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTOS_STATE_DIR", str(tmp_path / "state"))
    cfg_path = tmp_path / "config.toml"
    result = runner.invoke(
        app,
        [
            "config",
            "set",
            "skills.max_skills_prompt_chars",
            "32000",
            "--config",
            str(cfg_path),
        ],
    )
    assert result.exit_code == 0, result.output
    loaded = load_config(cfg_path)
    assert loaded.skills.max_skills_prompt_chars == 32000


def test_config_set_unknown_key_still_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTOS_STATE_DIR", str(tmp_path / "state"))
    cfg_path = tmp_path / "config.toml"
    result = runner.invoke(
        app,
        ["config", "set", "skills.no_such_key", "x", "--config", str(cfg_path)],
    )
    assert result.exit_code == 1
    assert "Key not found" in result.output
    assert not cfg_path.exists()


def test_config_set_env_hint_rejects_unknown_key() -> None:
    result = runner.invoke(app, ["config", "set", "gateway.port", "18791"])
    assert result.exit_code == 1
    assert "Key not found" in result.output
    assert "export " not in result.output.lower()


def test_config_set_env_hint_rejects_skills_config_map() -> None:
    result = runner.invoke(app, ["config", "set", "skills.config.wiki.path", "/srv/wiki"])
    assert result.exit_code == 1
    assert "Key not found" in result.output
    assert "export " not in result.output.lower()


def test_config_set_env_hint_still_prints_for_existing_field() -> None:
    result = runner.invoke(app, ["config", "set", "skills.max_skills_prompt_chars", "32000"])
    assert result.exit_code == 0, result.output
    assert "export AGENTOS_GATEWAY_SKILLS__MAX_SKILLS_PROMPT_CHARS=32000" in result.output

