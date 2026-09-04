"""Regression coverage for Discord heartbeat-initiated reconnects (issue #882)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentos.channels.discord import DiscordChannel, DiscordChannelConfig


class _FakeWebSocket:
    def __init__(self, url: str) -> None:
        self.url = url
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _stub_gateway_reconnect(channel: DiscordChannel) -> list[str]:
    connected_urls: list[str] = []

    async def connect_ws(url: str) -> _FakeWebSocket:
        connected_urls.append(url)
        return _FakeWebSocket(url)

    async def recv() -> dict[str, Any]:
        return {"d": {"heartbeat_interval": 60_000}}

    async def send(_payload: dict[str, Any]) -> None:
        return None

    channel._connect_ws = connect_ws  # type: ignore[method-assign]
    channel._ws_recv = recv  # type: ignore[method-assign]
    channel._ws_send = send  # type: ignore[method-assign]
    channel._state.resume_url = "wss://resume.example.test"  # noqa: SLF001
    channel._connected = True  # noqa: SLF001
    return connected_urls


@pytest.mark.asyncio
async def test_heartbeat_timeout_does_not_cancel_its_own_reconnect() -> None:
    channel = DiscordChannel(DiscordChannelConfig(token="token"))
    connected_urls = _stub_gateway_reconnect(channel)
    channel._state.last_heartbeat_ack = False  # noqa: SLF001

    heartbeat_task = asyncio.create_task(channel._heartbeat_loop())  # noqa: SLF001
    channel._heartbeat_task = heartbeat_task  # noqa: SLF001

    # Let the heartbeat loop detect missed ACK and complete reconnect
    await heartbeat_task

    assert connected_urls == ["wss://resume.example.test"]
    assert channel._reconnect_generation == 1  # noqa: SLF001

    # Assert new socket exists
    assert channel._ws is not None  # noqa: SLF001
    assert isinstance(channel._ws, _FakeWebSocket)
    assert not channel._ws.closed

    # Assert new replacement heartbeat task exists
    replacement = channel._heartbeat_task  # noqa: SLF001
    assert replacement is not None
    assert replacement is not heartbeat_task
    assert not replacement.done()

    channel._connected = False  # noqa: SLF001
    replacement.cancel()
    await asyncio.gather(replacement, return_exceptions=True)


@pytest.mark.asyncio
async def test_external_reconnect_cancels_obsolete_heartbeat() -> None:
    channel = DiscordChannel(DiscordChannelConfig(token="token"))
    _stub_gateway_reconnect(channel)

    obsolete = asyncio.create_task(asyncio.Event().wait())
    channel._heartbeat_task = obsolete  # noqa: SLF001

    await channel._do_reconnect()  # noqa: SLF001
    await asyncio.sleep(0)

    # Assert obsolete heartbeat was cancelled
    assert obsolete.cancelled()

    # Assert new socket and replacement heartbeat task exist
    assert channel._ws is not None  # noqa: SLF001
    replacement = channel._heartbeat_task  # noqa: SLF001
    assert replacement is not None
    assert replacement is not obsolete

    channel._connected = False  # noqa: SLF001
    replacement.cancel()
    await asyncio.gather(replacement, return_exceptions=True)


@pytest.mark.asyncio
async def test_stop_cancels_heartbeat_task() -> None:
    channel = DiscordChannel(DiscordChannelConfig(token="token"))
    _stub_gateway_reconnect(channel)

    running_task = asyncio.create_task(asyncio.Event().wait())
    channel._heartbeat_task = running_task  # noqa: SLF001
    channel._connected = True  # noqa: SLF001

    await channel.stop()
    await asyncio.sleep(0)

    assert running_task.cancelled()
    assert channel._heartbeat_task is None  # noqa: SLF001
    assert not channel._connected  # noqa: SLF001
