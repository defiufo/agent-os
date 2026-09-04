"""Control UI route exemptions cannot swallow protected API routes."""

from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient

import agentos.gateway.rpc_config  # noqa: F401  ensure registration
from agentos.gateway.app import create_gateway_app
from agentos.gateway.config import GatewayConfig
from agentos.gateway.rpc import RpcContext, get_dispatcher


def _config(tmp_path, *, base_path: str, max_requests: int = 120) -> GatewayConfig:
    return GatewayConfig(
        config_path=str(tmp_path / "config.toml"),
        auth={"mode": "token", "token": "test-token"},
        control_ui={"base_path": base_path},
        rate_limit={
            "enabled": True,
            "max_requests": max_requests,
            "window_seconds": 60,
        },
    )


def _patch_base_path(config: GatewayConfig, base_path: str):
    context = RpcContext(
        conn_id="ui-exemption-test",
        config=config,
    )
    result = asyncio.run(
        get_dispatcher().dispatch(
            "r1",
            "config.patch",
            {"patches": {"control_ui.base_path": base_path}},
            context,
        )
    )
    return result


@pytest.mark.parametrize("base_path", ["/", "/api", "/api/v1"])
def test_runtime_ui_base_path_change_cannot_disable_auth(tmp_path, base_path: str) -> None:
    config = _config(tmp_path, base_path="/control")
    with TestClient(create_gateway_app(config), base_url="http://localhost") as client:
        authorized = client.get(
            "/api/config",
            headers={"authorization": "Bearer test-token"},
        )
        assert authorized.status_code != 401

        result = _patch_base_path(config, base_path)
        assert result.error is not None
        assert config.control_ui.base_path == "/control"
        unauthorized = client.get("/api/config")
        assert unauthorized.status_code == 401


@pytest.mark.parametrize("base_path", ["/", "/api", "/api/v1"])
def test_runtime_ui_base_path_change_cannot_disable_rate_limit(tmp_path, base_path: str) -> None:
    config = _config(tmp_path, base_path="/control", max_requests=2)
    headers = {"authorization": "Bearer test-token"}
    with TestClient(create_gateway_app(config), base_url="http://localhost") as client:
        first = client.get("/api/config", headers=headers)
        assert first.status_code != 429

        result = _patch_base_path(config, base_path)
        assert result.error is not None
        assert config.control_ui.base_path == "/control"
        second = client.get("/api/config", headers=headers)
        third = client.get("/api/config", headers=headers)
        assert second.status_code != 429
        assert third.status_code == 429


def test_http_token_auth_without_configured_token_fails_closed(tmp_path) -> None:
    config = GatewayConfig(
        config_path=str(tmp_path / "config.toml"),
        auth={"mode": "token", "token": None},
    )

    with TestClient(create_gateway_app(config), base_url="http://localhost") as client:
        response = client.get("/api/config")

    assert response.status_code == 401


def test_http_token_auth_rejects_wrong_token(tmp_path) -> None:
    config = GatewayConfig(
        config_path=str(tmp_path / "config.toml"),
        auth={"mode": "token", "token": "test-token"},
    )

    with TestClient(create_gateway_app(config), base_url="http://localhost") as client:
        response = client.get(
            "/api/config",
            headers={"authorization": "Bearer nope"},
        )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


def test_control_ui_api_subtree_is_rate_limited_while_shell_is_exempt(tmp_path) -> None:
    config = _config(tmp_path, base_path="/control", max_requests=2)
    headers = {"authorization": "Bearer test-token"}
    with TestClient(create_gateway_app(config), base_url="http://localhost") as client:
        # API calls under /control/api/ are rate-limited
        r1 = client.get("/control/api/config", headers=headers)
        r2 = client.get("/control/api/config", headers=headers)
        r3 = client.get("/control/api/config", headers=headers)
        assert r1.status_code != 429
        assert r2.status_code != 429
        assert r3.status_code == 429

        # Static UI shell under /control is exempt from rate limiting
        for _ in range(5):
            shell_resp = client.get("/control/")
            assert shell_resp.status_code != 429
