"""Tests for automatic router tier model fallback in Auto Pilot.

When Auto Pilot routes to a model (e.g. c1 -> gpt-5.6-luna) and that model
fails (e.g. upstream timeout, 503, or connection drop), the system automatically
falls back to alternative models in the router profile (e.g. c2 -> glm-5.3)
without requiring manual cancellation or waiting through multiple long retry loops.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from agentos.engine.pipeline import TurnContext
from agentos.engine.runtime import (
    _derive_router_tier_fallbacks,
    _SelectorFallbackProvider,
)
from agentos.gateway.config import AgentOSRouterConfig, GatewayConfig
from agentos.provider import DoneEvent as ProviderDone
from agentos.provider import ErrorEvent as ProviderError
from agentos.provider import ProviderHeartbeatEvent
from agentos.provider import TextDeltaEvent as ProviderText
from agentos.provider.selector import ModelSelector, ProviderConfig, SelectorConfig

pytestmark = pytest.mark.anyio


class _StubProvider:
    def __init__(self, name: str, model: str, streams: list[list[Any]]) -> None:
        self.provider_name = name
        self._model = model
        self._streams = streams
        self.calls = 0

    def chat(self, messages: list[Any], tools: Any = None, config: Any = None) -> AsyncIterator:
        index = min(self.calls, len(self._streams) - 1)
        self.calls += 1
        return self._stream(self._streams[index])

    async def _stream(self, events: list[Any]) -> AsyncIterator[Any]:
        for event in events:
            yield event

    async def list_models(self) -> list[Any]:
        return []


async def _drain(wrapper: _SelectorFallbackProvider) -> list[Any]:
    return [event async for event in wrapper.chat([], tools=None, config=None)]


def test_derive_router_tier_fallbacks_for_c1() -> None:
    """Auto Pilot on c1 generates c2, c0, c3 fallback ProviderConfigs."""
    router_cfg = AgentOSRouterConfig(
        enabled=True,
        tiers={
            "c0": {"provider": "opencap", "model": "deepseek-v4-flash"},
            "c1": {"provider": "opencap", "model": "gpt-5.6-luna"},
            "c2": {"provider": "opencap", "model": "glm-5.3"},
            "c3": {"provider": "opencap", "model": "claude-opus-5"},
            "image_model": {"provider": "opencap", "model": "nano-banana", "image_only": True},
        },
    )
    gw_config = GatewayConfig(llm={"provider": "opencap", "model": "gpt-5.6-luna", "api_key": "k"})
    gw_config.agentos_router = router_cfg

    turn = TurnContext(
        message="bisa trading gk kamu?",
        session_key="test-key",
        config=gw_config,
        provider=None,
        model="gpt-5.6-luna",
        tool_defs=[],
        system_prompt="sys",
        metadata={"routing_applied": True, "routed_tier": "c1"},
    )

    primary_cfg = ProviderConfig(
        provider="opencap",
        model="gpt-5.6-luna",
        api_key="k",
        base_url="https://api.opencap.ai",
    )
    selector = ModelSelector(SelectorConfig(primary=primary_cfg))

    fallbacks = _derive_router_tier_fallbacks(turn, selector)
    assert fallbacks is not None
    # c1 preferred order: c2 first, then c0, then c3; image_only skipped; gpt-5.6-luna skipped
    assert [f.model for f in fallbacks] == ["glm-5.3", "deepseek-v4-flash", "claude-opus-5"]
    assert all(f.provider == "opencap" for f in fallbacks)
    assert all(f.api_key == "k" for f in fallbacks)


def test_derive_router_tier_fallbacks_ignored_when_routing_not_applied() -> None:
    """When user manually pins a model (no auto routing), no auto tier fallbacks are generated."""
    gw_config = GatewayConfig(llm={"provider": "opencap", "model": "gpt-5.6-luna", "api_key": "k"})
    turn = TurnContext(
        message="hi",
        session_key="test-key",
        config=gw_config,
        provider=None,
        model="gpt-5.6-luna",
        tool_defs=[],
        system_prompt="sys",
        metadata={"routing_applied": False},
    )
    selector = ModelSelector(
        SelectorConfig(primary=ProviderConfig("opencap", "gpt-5.6-luna", api_key="k"))
    )
    assert _derive_router_tier_fallbacks(turn, selector) is None


async def test_auto_fallback_switches_model_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """When primary model (gpt-5.6-luna) times out, _SelectorFallbackProvider automatically
    switches to the next fallback model (glm-5.3) and yields its tokens."""
    from agentos.provider import selector as selector_module

    providers_created: list[tuple[str, str]] = []

    def _stub_build(cfg: ProviderConfig) -> _StubProvider:
        providers_created.append((cfg.provider, cfg.model))
        if cfg.model == "gpt-5.6-luna":
            # Primary hangs/times out
            return _StubProvider(
                cfg.provider,
                cfg.model,
                [[ProviderError(code="timeout", message="Request timed out: ReadTimeout")]],
            )
        # Fallback model succeeds
        return _StubProvider(
            cfg.provider,
            cfg.model,
            [[ProviderText(text="Hello from GLM 5.3!"), ProviderDone(stop_reason="stop")]],
        )

    monkeypatch.setattr(selector_module, "_build_provider", _stub_build)

    primary_cfg = ProviderConfig("opencap", "gpt-5.6-luna", api_key="k")
    fallback_cfg = ProviderConfig("opencap", "glm-5.3", api_key="k")
    selector = ModelSelector(SelectorConfig(primary=primary_cfg, fallbacks=[fallback_cfg]))

    initial_provider = selector.resolve()
    fallback_wrapper = _SelectorFallbackProvider(initial_provider, selector)

    events = await _drain(fallback_wrapper)

    # 1. Heartbeat event was emitted to notify user of the automatic switch
    heartbeats = [e for e in events if isinstance(e, ProviderHeartbeatEvent)]
    assert len(heartbeats) == 1
    assert heartbeats[0].phase == "llm_fallback"
    assert "gpt-5.6-luna" in heartbeats[0].message
    assert "glm-5.3" in heartbeats[0].message

    # 2. Text from fallback model was emitted
    text_events = [e for e in events if isinstance(e, ProviderText)]
    assert len(text_events) == 1
    assert text_events[0].text == "Hello from GLM 5.3!"

    # 3. Both providers were built
    assert ("opencap", "gpt-5.6-luna") in providers_created
    assert ("opencap", "glm-5.3") in providers_created


async def test_auto_fallback_cascades_if_first_fallback_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If primary fails AND first fallback fails, it cascades to the second fallback."""
    from agentos.provider import selector as selector_module

    def _stub_build(cfg: ProviderConfig) -> _StubProvider:
        if cfg.model in {"gpt-5.6-luna", "glm-5.3"}:
            return _StubProvider(
                cfg.provider,
                cfg.model,
                [[ProviderError(code="503", message="temporary overload")]],
            )
        return _StubProvider(
            cfg.provider,
            cfg.model,
            [[ProviderText(text="Hello from DeepSeek!"), ProviderDone(stop_reason="stop")]],
        )

    monkeypatch.setattr(selector_module, "_build_provider", _stub_build)

    selector = ModelSelector(
        SelectorConfig(
            primary=ProviderConfig("opencap", "gpt-5.6-luna", api_key="k"),
            fallbacks=[
                ProviderConfig("opencap", "glm-5.3", api_key="k"),
                ProviderConfig("opencap", "deepseek-v4-flash", api_key="k"),
            ],
        )
    )

    initial_provider = selector.resolve()
    fallback_wrapper = _SelectorFallbackProvider(initial_provider, selector)

    events = await _drain(fallback_wrapper)

    text_events = [e for e in events if isinstance(e, ProviderText)]
    assert len(text_events) == 1
    assert text_events[0].text == "Hello from DeepSeek!"
