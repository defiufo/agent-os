"""Discord dispatch loop must keep reading after a reconnect.

A gateway drop, opcode 7, or opcode 9 used to ``return`` from
``_dispatch_loop`` after ``_reconnect()``. Heartbeat was restarted inside
``_do_reconnect``, but dispatch was not — ``health_check`` still reported
connected, yet MESSAGE_CREATE / slash commands were never read again.
"""

from __future__ import annotations

from typing import Any

import pytest
import websockets.exceptions

from agentos.channels.discord import DiscordChannel, DiscordChannelConfig


def _message_create(content: str = "hello after reconnect") -> dict[str, Any]:
    return {
        "op": 0,
        "s": 1,
        "t": "MESSAGE_CREATE",
        "d": {
            "id": "msg-1",
            "channel_id": "channel-1",
            "channel_type": 1,
            "author": {"id": "user-1"},
            "content": content,
        },
    }


async def _drive_dispatch(channel: DiscordChannel, frames: list[object]) -> int:
    """Play *frames* through ``_dispatch_loop`` and return reconnect count.

    After the scripted frames, the loop is stopped by clearing ``_connected``
    so the test does not hang on a real socket.
    """
    pending = list(frames)
    reconnects = 0

    async def recv() -> dict[str, Any]:
        if not pending:
            channel._connected = False
            return {"op": 11}
        item = pending.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item  # type: ignore[return-value]

    async def reconnect() -> None:
        nonlocal reconnects
        reconnects += 1

    channel._ws_recv = recv  # type: ignore[method-assign]
    channel._reconnect = reconnect  # type: ignore[method-assign]
    channel._connected = True
    channel.bot_user_id = "bot"
    await channel._dispatch_loop()
    return reconnects


@pytest.mark.asyncio
async def test_dispatch_loop_reads_events_after_opcode_7_reconnect() -> None:
    channel = DiscordChannel(DiscordChannelConfig(token="token"))
    reconnects = await _drive_dispatch(channel, [{"op": 7}, _message_create()])
    assert reconnects == 1
    msg = channel._queue.get_nowait()
    assert msg.content == "hello after reconnect"


@pytest.mark.asyncio
async def test_dispatch_loop_reads_events_after_connection_closed() -> None:
    channel = DiscordChannel(DiscordChannelConfig(token="token"))
    closed = websockets.exceptions.ConnectionClosedOK(rcvd=None, sent=None)
    reconnects = await _drive_dispatch(channel, [closed, _message_create()])
    assert reconnects == 1
    msg = channel._queue.get_nowait()
    assert msg.content == "hello after reconnect"
