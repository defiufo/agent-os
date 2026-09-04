from __future__ import annotations

import hmac
from typing import Any

from agentos.gateway.access import ConnectionSurface
from agentos.gateway.auth import resolve_auth, token_matches
from agentos.gateway.config import AuthConfig, GatewayConfig


def test_open_auth_loopback_admits_control_without_credentials() -> None:
    access = resolve_auth(
        GatewayConfig(debug=False, host="127.0.0.1"),
        {},
        "control",
        peer_ip="127.0.0.1",
    )

    assert access is not None
    assert access.surface is ConnectionSurface.CONTROL
    assert access.admitted is True
    assert access.credential_verified is False


def test_open_auth_public_listener_fails_even_for_loopback_peer() -> None:
    access = resolve_auth(
        GatewayConfig(debug=False, host="0.0.0.0"),
        {},
        "control",
        peer_ip="127.0.0.1",
    )

    assert access is None


def test_token_auth_admits_complete_control_surface() -> None:
    access = resolve_auth(
        GatewayConfig(
            host="0.0.0.0",
            auth=AuthConfig(mode="token", token="secret"),
        ),
        {"token": "secret"},
        "control",
        peer_ip="203.0.113.7",
    )

    assert access is not None
    assert access.surface is ConnectionSurface.CONTROL
    assert access.admitted is True
    assert access.credential_verified is True


def test_token_auth_without_configured_token_fails_closed() -> None:
    config = GatewayConfig(auth=AuthConfig(mode="token", token=None))

    assert resolve_auth(config, {}, "control", peer_ip="127.0.0.1") is None


def test_token_auth_rejects_wrong_token() -> None:
    config = GatewayConfig(
        host="0.0.0.0",
        auth=AuthConfig(mode="token", token="secret"),
    )

    assert resolve_auth(config, {"token": "nope"}, "control", peer_ip="203.0.113.7") is None
    assert resolve_auth(config, {"token": 123}, "control", peer_ip="203.0.113.7") is None
    assert resolve_auth(config, {}, "control", peer_ip="203.0.113.7") is None


def test_token_matches_accepts_equal_strings() -> None:
    assert token_matches("secret", "secret") is True


def test_token_matches_rejects_mismatch_missing_empty_and_non_string() -> None:
    assert token_matches("secret", "secret!") is False
    assert token_matches("secre", "secret") is False
    assert token_matches(None, "secret") is False
    assert token_matches("", "secret") is False
    assert token_matches("secret", None) is False
    assert token_matches("secret", "") is False
    assert token_matches(None, None) is False
    bogus: Any = 123
    assert token_matches(bogus, "secret") is False
    assert token_matches("secret", bogus) is False


def test_token_matches_uses_compare_digest(monkeypatch) -> None:
    calls: list[tuple[bytes | str, bytes | str]] = []
    original = hmac.compare_digest

    def _capture(left: bytes | str, right: bytes | str) -> bool:
        calls.append((left, right))
        return original(left, right)

    monkeypatch.setattr("agentos.gateway.auth.hmac.compare_digest", _capture)

    assert token_matches("secret", "secret") is True
    assert calls == [(b"secret", b"secret")]
    calls.clear()
    assert token_matches("nope", "secret") is False
    assert calls == [(b"nope", b"secret")]
