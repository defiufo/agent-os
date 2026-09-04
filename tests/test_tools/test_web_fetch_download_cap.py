"""Tests: web_fetch downloads are capped at a hard byte limit.

The display cap (max_chars) only controls what the model sees. Without a
download ceiling, a single response with an unbounded body (chunked encoding,
no content-length, or a lying content-length) is buffered fully into memory
before any truncation is applied, so one URL can exhaust the process.
"""

from __future__ import annotations

import json
from typing import Any
from unittest import mock

import httpx
import pytest

from agentos.sandbox.config import SandboxSettings
from agentos.sandbox.integration import configure_runtime, reset_runtime
from agentos.tools.builtin import web_fetch as wf
from agentos.tools.builtin.web_fetch import (
    _WEB_FETCH_DOWNLOAD_LIMIT_BYTES,
    _resolve_download_limit_bytes,
)


@pytest.fixture
def sandbox_off(tmp_path: Any) -> Any:
    """Configure a sandbox-off runtime so the @sandboxed tool runs inline."""
    from pathlib import Path

    configure_runtime(
        SandboxSettings(sandbox=False, security_grading=False, allow_legacy_mode=True),
        workspace=Path(tmp_path),
    )
    yield
    reset_runtime()


def _e2e_resolver(addr: str) -> Any:
    """Return a socket.getaddrinfo replacement resolving any host to addr."""

    def resolver(host: Any, port: Any, **_kw: Any) -> list[tuple[Any, ...]]:
        return [(2, 1, 6, "", (addr, 0))]

    return resolver


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return real_async_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("socket.getaddrinfo", _e2e_resolver("93.184.216.34"))
    monkeypatch.setattr(wf.httpx, "AsyncClient", fake_async_client)


class _StreamingBody(httpx.AsyncByteStream):
    """A body served as an async iterator, not a pre-buffered blob."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def __aiter__(self) -> Any:
        yield self._data


class _StreamingTransport(httpx.AsyncBaseTransport):
    """A genuinely streaming transport.

    Unlike ``httpx.MockTransport`` (which hands back a fully-buffered stream),
    this one streams the body, so reading a closed response actually raises.
    """

    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self._status = status
        self._headers = headers
        self._body = body

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            self._status, headers=self._headers, stream=_StreamingBody(self._body)
        )


def _install_streaming_transport(
    monkeypatch: pytest.MonkeyPatch, transport: _StreamingTransport
) -> None:
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return real_async_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr("socket.getaddrinfo", _e2e_resolver("93.184.216.34"))
    monkeypatch.setattr(wf.httpx, "AsyncClient", fake_async_client)


def test_download_limit_default_and_env_override() -> None:
    assert _resolve_download_limit_bytes() == _WEB_FETCH_DOWNLOAD_LIMIT_BYTES

    with mock.patch.dict("os.environ", {"AGENTOS_WEB_FETCH_DOWNLOAD_LIMIT": "131072"}):
        assert _resolve_download_limit_bytes() == 131_072

    with mock.patch.dict("os.environ", {"AGENTOS_WEB_FETCH_DOWNLOAD_LIMIT": "100"}):
        # Values below the 64 KiB floor fall back to the default.
        assert _resolve_download_limit_bytes() == _WEB_FETCH_DOWNLOAD_LIMIT_BYTES

    with mock.patch.dict("os.environ", {"AGENTOS_WEB_FETCH_DOWNLOAD_LIMIT": "notanint"}):
        assert _resolve_download_limit_bytes() == _WEB_FETCH_DOWNLOAD_LIMIT_BYTES


@pytest.mark.asyncio
async def test_web_fetch_caps_unbounded_response_body(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_off: Any,
) -> None:
    """A 2 MiB response is buffered only up to the download limit."""
    limit = 128 * 1024

    def handler(_request: httpx.Request) -> httpx.Response:
        # Non-HTML so the raw body is returned and its length is measurable.
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"A" * (2 * 1024 * 1024),
        )

    _install_transport(monkeypatch, handler)
    monkeypatch.setattr(wf, "_WEB_FETCH_DOWNLOAD_LIMIT_BYTES", limit)
    wf._cache.clear()

    result = json.loads(await wf.web_fetch(url="https://example.com/huge"))

    assert result["status"] == 200
    assert result["truncated"] is True
    # The buffered body must not exceed the download limit.
    assert result["length"] <= limit


@pytest.mark.asyncio
async def test_web_fetch_small_body_not_truncated(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_off: Any,
) -> None:
    """A small body passes through without download truncation."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"hello world")

    _install_transport(monkeypatch, handler)
    wf._cache.clear()

    result = json.loads(await wf.web_fetch(url="https://example.com/small"))

    assert result["status"] == 200
    assert result["truncated"] is False
    assert result["length"] == len("hello world")
    assert "hello world" in str(result["text"])


@pytest.mark.asyncio
async def test_web_fetch_redirect_without_location_returns_body(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_off: Any,
) -> None:
    """A 3xx response with no Location header is the final response.

    The body must be returned, not an error about a closed stream. Uses a
    genuinely streaming transport: a buffered MockTransport cannot catch the
    read-after-aclose regression.
    """
    transport = _StreamingTransport(
        302,
        {"content-type": "text/plain"},
        b"moved, but nowhere",
    )
    _install_streaming_transport(monkeypatch, transport)
    wf._cache.clear()

    result = json.loads(await wf.web_fetch(url="https://example.com/no-location"))

    assert result["status"] == 302
    assert "closed" not in str(result.get("error", ""))
    assert "moved, but nowhere" in str(result["text"])


@pytest.mark.asyncio
async def test_web_fetch_honors_response_charset(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_off: Any,
) -> None:
    """Non-UTF-8 (ISO-8859-1) pages must be decoded with the server's charset.

    Mirrors ``test_http_request_honors_response_charset`` from the #509 fix
    on the sibling ``http_request`` tool: the streaming transport serves an
    ISO-8859-1 body, the Content-Type advertises ``charset=iso-8859-1``, and
    the returned text must contain the real ``café`` character — not the
    U+FFFD replacement character that a hard-coded UTF-8 decode would emit.
    """
    body_bytes = b"caf\xe9 na\xefve r\xe9sum\xe9 ..."
    transport = _StreamingTransport(
        200,
        {"content-type": "text/plain; charset=iso-8859-1"},
        body_bytes,
    )
    _install_streaming_transport(monkeypatch, transport)
    wf._cache.clear()

    result = json.loads(await wf.web_fetch(url="https://example.com/latin1"))

    assert result["status"] == 200
    assert result["content_type"] == "text/plain; charset=iso-8859-1"
    inner_text = str(result["text"])
    assert "café" in inner_text
    assert "\ufffd" not in inner_text
