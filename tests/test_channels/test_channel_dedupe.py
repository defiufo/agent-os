from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from agentos.channels.discord import DiscordChannel, DiscordChannelConfig
from agentos.channels.msteams import MSTeamsChannel, MSTeamsChannelConfig
from agentos.channels.slack import SlackChannel
from agentos.channels.types import IncomingMessage

_SIGNING_SECRET = "s3cr3t"


class _BodyRequest:
    def __init__(self, payload: dict, *, signing_secret: str = _SIGNING_SECRET) -> None:
        self._body = json.dumps(payload).encode()
        timestamp = str(int(time.time()))
        digest = hmac.HMAC(
            signing_secret.encode(),
            f"v0:{timestamp}:{self._body.decode()}".encode(),
            hashlib.sha256,
        ).hexdigest()
        self.headers: dict[str, str] = {
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": f"v0={digest}",
        }

    async def body(self) -> bytes:
        return self._body


@pytest.mark.asyncio
async def test_slack_webhook_dedupes_retried_event_callback() -> None:
    channel = SlackChannel(token="xoxb-test", slack_channel_id="C1", signing_secret=_SIGNING_SECRET)
    payload = {
        "type": "event_callback",
        "event_id": "Ev123",
        "event": {
            "type": "message",
            "user": "U1",
            "channel": "C1",
            "text": "draw an image",
            "ts": "1710000000.000100",
            "channel_type": "im",
        },
    }

    await channel._handle_webhook(_BodyRequest(payload))  # noqa: SLF001
    await channel._handle_webhook(_BodyRequest(payload))  # noqa: SLF001

    assert channel._queue.qsize() == 1  # noqa: SLF001
    assert (await channel.receive()).content == "draw an image"


@pytest.mark.asyncio
async def test_msteams_enqueue_dedupes_retried_activity_id() -> None:
    channel = MSTeamsChannel(MSTeamsChannelConfig())
    msg = IncomingMessage(
        sender_id="u1",
        channel_id="conv1",
        content="make a deck",
        metadata={"activity_id": "activity-1"},
    )

    channel.enqueue(msg)
    channel.enqueue(msg)

    assert channel._queue.qsize() == 1  # noqa: SLF001
    assert (await channel.receive()).content == "make a deck"


@pytest.mark.asyncio
async def test_discord_gateway_dedupes_replayed_message_create() -> None:
    channel = DiscordChannel(DiscordChannelConfig(token="token"))
    payload = {
        "id": "message-1",
        "channel_id": "channel-1",
        "content": "hello",
        "author": {"id": "user-1"},
        "mentions": [],
        "attachments": [],
    }

    await channel._handle_dispatch("MESSAGE_CREATE", payload)  # noqa: SLF001
    await channel._handle_dispatch("MESSAGE_CREATE", payload)  # noqa: SLF001

    assert channel._queue.qsize() == 1  # noqa: SLF001
    assert (await channel.receive()).content == "hello"
