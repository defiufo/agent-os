"""Regression tests for ``reconcile_routing_metadata``.

An explicit model (a durable ``config.agents[].model``, a session pin, an API
override) beats the Pilot Router's pick when ``PromptAssemblerStage`` resolves
the final model. Before the reconcile step the router metadata kept claiming
the route had been applied, so the HUD event, the DoneEvent, per-turn usage and
the savings figures all named a model the provider was never asked for.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from agentos.engine.pipeline import TurnContext
from agentos.engine.router_decision import (
    ROUTING_OVERRIDE_EXPLICIT_MODEL,
    build_router_decision_event,
    reconcile_routing_metadata,
)


def _ctx(metadata: dict[str, Any], model: str = "") -> TurnContext:
    return TurnContext(
        message="hi",
        session_key="agent:main:webchat:abc",
        config=SimpleNamespace(),
        provider=None,
        model=model,
        tool_defs=[],
        system_prompt="",
        metadata=metadata,
    )


def _routed_metadata() -> dict[str, Any]:
    return {
        "routed_tier": "c0",
        "routed_model": "minimax-m3",
        "applied_model": "minimax-m3",
        "baseline_model": "claude-opus-4.8",
        "routing_applied": True,
        "routing_source": "router",
        "routing_confidence": 0.82,
        "savings_pct": 87.5,
        "savings_max_price_per_m": 2.0,
        "savings_routed_price_per_m": 0.08,
    }


def test_noop_when_router_did_not_fire() -> None:
    metadata: dict[str, Any] = {"applied_model": "minimax-m3"}
    assert reconcile_routing_metadata(metadata, "claude-opus-4.8") is False
    assert metadata == {"applied_model": "minimax-m3"}


def test_noop_when_resolved_model_is_empty() -> None:
    metadata = _routed_metadata()
    assert reconcile_routing_metadata(metadata, "") is False
    assert metadata == _routed_metadata()


def test_noop_when_the_route_is_what_actually_ran() -> None:
    metadata = _routed_metadata()
    assert reconcile_routing_metadata(metadata, "minimax-m3") is False
    assert metadata == _routed_metadata()


def test_noop_when_router_left_no_applied_model() -> None:
    metadata = _routed_metadata()
    del metadata["applied_model"]
    before = dict(metadata)
    assert reconcile_routing_metadata(metadata, "claude-opus-4.8") is False
    assert metadata == before


def test_demotes_route_the_explicit_model_overrode() -> None:
    metadata = _routed_metadata()
    assert reconcile_routing_metadata(metadata, "claude-opus-4.8") is True

    assert metadata["routing_applied"] is False
    assert metadata["routing_override_source"] == ROUTING_OVERRIDE_EXPLICIT_MODEL
    # The model that actually ran, on both fields that name it.
    assert metadata["applied_model"] == "claude-opus-4.8"
    assert metadata["baseline_model"] == "claude-opus-4.8"
    # Savings against a price nobody was billed are not savings.
    assert metadata["savings_pct"] == 0.0
    assert metadata["savings_max_price_per_m"] == 0.0
    assert metadata["savings_routed_price_per_m"] == 0.0
    # The router's advice stays on the record, exactly as in observe phase.
    assert metadata["routed_tier"] == "c0"
    assert metadata["routed_model"] == "minimax-m3"
    assert metadata["routing_source"] == "router"
    assert metadata["routing_confidence"] == 0.82


def test_reconciled_metadata_reaches_the_router_decision_event() -> None:
    metadata = _routed_metadata()
    reconcile_routing_metadata(metadata, "claude-opus-4.8")

    event = build_router_decision_event(_ctx(metadata, model="minimax-m3"))
    assert event is not None
    assert event.routing_applied is False
    assert event.savings_pct == 0.0
    assert event.baseline_model == "claude-opus-4.8"
    # Advisory, and flagged as such by routing_applied.
    assert event.tier == "c0"
    assert event.model == "minimax-m3"
