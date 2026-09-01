"""Cap accumulated Teams stream text so send_streaming cannot grow without bound."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from agentos.channels.msteams import (
    MSTeamsChannel,
    MSTeamsChannelConfig,
    _MAX_STREAM_ACCUMULATED_CHARS,
)


async def _stream(*chunks: str) -> AsyncIterator[str]:
    for chunk in chunks:
        yield chunk


class _FakeAdapter:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.updated: list[str] = []

    async def continue_conversation(self, _ref: Any, callback: Any, bot_id: Any = None) -> None:
        ctx = SimpleNamespace(
            send_activity=self._send_activity,
            update_activity=self._update_activity,
        )
        await callback(ctx)

    async def _send_activity(self, text: str) -> SimpleNamespace:
        self.sent.append(text)
        return SimpleNamespace(id="msg-1")

    async def _update_activity(self, activity: Any) -> None:
        self.updated.append(getattr(activity, "text", ""))


def _channel() -> MSTeamsChannel:
    channel = MSTeamsChannel(MSTeamsChannelConfig(edit_interval_s=0.0))
    channel._adapter = _FakeAdapter()  # noqa: SLF001
    channel._bot_id = "bot"
    channel._references["conv-1"] = SimpleNamespace(conversation=SimpleNamespace(id="conv-1"))  # noqa: SLF001
    return channel


@pytest.mark.asyncio
async def test_send_streaming_caps_accumulated_text() -> None:
    channel = _channel()
    adapter: _FakeAdapter = channel._adapter  # type: ignore[assignment]  # noqa: SLF001
    over = "x" * (_MAX_STREAM_ACCUMULATED_CHARS + 50_000)

    message_id = await channel.send_streaming(_stream(over), reply_to="conv-1")

    assert message_id == "msg-1"
    assert adapter.sent
    assert len(adapter.sent[0]) == _MAX_STREAM_ACCUMULATED_CHARS


@pytest.mark.asyncio
async def test_send_streaming_caps_across_chunks() -> None:
    channel = _channel()
    adapter: _FakeAdapter = channel._adapter  # type: ignore[assignment]  # noqa: SLF001
    first = "a" * 80_000
    second = "b" * 80_000

    await channel.send_streaming(_stream(first, second), reply_to="conv-1")

    delivered = adapter.updated[-1] if adapter.updated else adapter.sent[-1]
    assert len(delivered) == _MAX_STREAM_ACCUMULATED_CHARS
    assert delivered.startswith("a" * 80_000)
    assert delivered.endswith("b" * 20_000)
