"""Regression: Telegram entity offsets are UTF-16 code units, not code points.

Telegram Bot API message-entity ``offset``/``length`` count UTF-16 code
units. When non-BMP characters (emoji, etc.) precede a mention or command,
slicing the Python ``str`` directly (which indexes by code point) drifts left
by one per non-BMP char and misaligns the extracted text.

For a ``bot_command`` entity this is not masked by the plain-text ``@bot``
fallback: a misaligned command target sets ``has_mismatched_bot_command`` and
``is_group_mentioned`` returns ``False`` *before* the fallback runs — so a
``/cmd@mybot`` genuinely addressed to the bot is silently ignored.
"""

from __future__ import annotations

from agentos.channels.telegram import TelegramChannel, TelegramChannelConfig
from agentos.channels.types import IncomingMessage


def _group_msg(content: str, entities: list[dict]) -> IncomingMessage:
    return IncomingMessage(
        sender_id="42",
        channel_id="-100999",
        content=content,
        metadata={"is_group": True, "content_entities": entities},
    )


def _channel() -> TelegramChannel:
    channel = TelegramChannel(TelegramChannelConfig(token="token"))
    channel.bot_username = "mybot"
    channel.bot_user_id = "555"
    return channel


def test_bot_command_addressed_to_us_after_emoji_is_recognized() -> None:
    """/cmd@mybot after two emojis must count as addressed to the bot.

    Two emojis drift a code-point slice by 2 units; the mid-string command
    slice becomes 'elp@mybot n' -> target 'mybot n' -> mismatch -> the method
    returns False at the has_mismatched_bot_command guard, never reaching the
    plain-text fallback. So this case is *not* masked by the fallback.
    """
    channel = _channel()
    text = "😀😀 /help@mybot now"
    entities = [{"type": "bot_command", "offset": 5, "length": len("/help@mybot")}]
    assert channel.is_group_mentioned(_group_msg(text, entities)) is True


def test_bot_command_for_other_bot_after_emoji_is_ignored() -> None:
    channel = _channel()
    text = "😀😀 /help@otherbot now"
    entities = [{"type": "bot_command", "offset": 5, "length": len("/help@otherbot")}]
    assert channel.is_group_mentioned(_group_msg(text, entities)) is False


def test_bare_command_without_target_after_emoji_is_recognized() -> None:
    """/help (no @target) in a group after emoji -> addressed to all bots."""
    channel = _channel()
    text = "😀 /help now"
    entities = [{"type": "bot_command", "offset": 3, "length": len("/help")}]
    assert channel.is_group_mentioned(_group_msg(text, entities)) is True


def test_mention_after_emoji_still_matches() -> None:
    channel = _channel()
    text = "😀😀 @mybot hi"
    entities = [{"type": "mention", "offset": 5, "length": len("@mybot")}]
    assert channel.is_group_mentioned(_group_msg(text, entities)) is True


def test_command_without_emoji_unaffected() -> None:
    channel = _channel()
    text = "/help@mybot now"
    entities = [{"type": "bot_command", "offset": 0, "length": len("/help@mybot")}]
    assert channel.is_group_mentioned(_group_msg(text, entities)) is True
