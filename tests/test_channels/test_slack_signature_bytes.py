"""Byte-faithful HMAC signature verification for Slack webhooks (fix #680).

The previous implementation called ``body.decode()`` (implicit UTF-8) which
raises ``UnicodeDecodeError`` on raw bytes that are not valid UTF-8 and also
fails to produce the correct digest for non-ASCII payloads when a lossy
work-around like ``latin-1`` is used.

The fix builds ``sig_basestring`` entirely in **bytes** so the digest is
always correct regardless of the body encoding.

These tests verify:
1. Pure ASCII body still verifies correctly.
2. Non-ASCII UTF-8 body (multi-byte emoji, CJK) verifies correctly.
3. Bodies that are NOT valid UTF-8 (raw ``0xFF``) do NOT raise and verify.
4. A wrong signature is still rejected.
"""

from __future__ import annotations

import hashlib
import hmac

from agentos.channels.slack import SlackChannel

SIGNING_SECRET = "test-signing-secret"
TIMESTAMP = "1234567890"


def _make_channel() -> SlackChannel:
    return SlackChannel(
        token="xoxb-test",
        slack_channel_id="C123",
        signing_secret=SIGNING_SECRET,
    )


def _sign(body: bytes) -> str:
    """Compute a valid Slack-style v0 HMAC signature for *body*."""
    sig_basestring = b"v0:" + TIMESTAMP.encode() + b":" + body
    return (
        "v0="
        + hmac.HMAC(
            SIGNING_SECRET.encode(),
            sig_basestring,
            hashlib.sha256,
        ).hexdigest()
    )


class TestByteFaithfulSignature:
    """Regression tests for the byte-faithful HMAC fix."""

    def test_ascii_body(self):
        """ASCII body produces the expected digest."""
        body = b"user=alice&text=hello"
        signature = _sign(body)
        assert _make_channel()._verify_signature(body, TIMESTAMP, signature)

    def test_utf8_body_emoji(self):
        """Non-ASCII UTF-8 body (emoji) verifies correctly."""
        body = b"user=alice&text=hello+%F0%9F%9A%80"
        signature = _sign(body)
        assert _make_channel()._verify_signature(body, TIMESTAMP, signature)

    def test_utf8_body_cjk(self):
        """Non-ASCII UTF-8 body (CJK characters) verifies correctly."""
        body = b"user=alice&text=%E4%BD%A0%E5%A5%BD"
        signature = _sign(body)
        assert _make_channel()._verify_signature(body, TIMESTAMP, signature)

    def test_raw_bytes_not_utf8(self):
        """Body with bytes that are NOT valid UTF-8 does NOT raise."""
        body = b"user=alice&data=\xff\xfe\xfd"
        signature = _sign(body)
        # This MUST NOT raise UnicodeDecodeError
        assert _make_channel()._verify_signature(body, TIMESTAMP, signature)

    def test_wrong_signature_rejected(self):
        """An incorrect signature is always rejected."""
        body = b"user=alice&text=hello"
        assert not _make_channel()._verify_signature(body, TIMESTAMP, "v0=deadbeef")

    def test_no_signing_secret(self):
        """Verification fails when no signing secret is configured."""
        ch = SlackChannel(token="xoxb-test", slack_channel_id="C123")
        assert not ch._verify_signature(b"data", TIMESTAMP, "v0=anything")

    def test_empty_body(self):
        """Empty body still produces a valid digest."""
        body = b""
        signature = _sign(body)
        assert _make_channel()._verify_signature(body, TIMESTAMP, signature)
