"""Regression tests for redirect handling in the media image fetcher.

`_fetch_image_url` follows redirects by hand so every hop is re-validated
against the SSRF guard. A redirect whose `Location` header is missing used to
close the response and `break` out of the loop, leaving the 3xx to be reported
by whatever failed first downstream: httpx's generic
"Redirect response '302 Found' for url ..." from `raise_for_status()`, or a
`StreamClosed` from reading the body that was just closed. Neither names the
malformed hop, so a dead-end redirect now gets its own message; the hop budget
around it has to keep working.
"""

from __future__ import annotations

import httpx
import pytest

from agentos.tools.builtin.media import _MAX_REDIRECTS, _fetch_image_url
from agentos.tools.types import ToolError


def _png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _patch_network(monkeypatch: pytest.MonkeyPatch, handler: object) -> None:
    real = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("transport", None)  # type: ignore[attr-defined]
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "socket.getaddrinfo", lambda h, p, **k: [(2, 1, 6, "", ("93.184.216.34", 0))]
    )
    monkeypatch.setattr(httpx, "AsyncClient", factory)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.asyncio
async def test_redirect_without_location_reports_the_malformed_redirect(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """A `Location`-less redirect names the URL and the missing header."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={})

    _patch_network(monkeypatch, handler)

    with pytest.raises(ToolError) as exc_info:
        await _fetch_image_url("https://example.com/test.png")

    message = str(exc_info.value)
    assert "https://example.com/test.png" in message
    assert "Location" in message
    # Not httpx's generic redirect/stream complaint wrapped by the caller.
    assert "Failed to fetch image from URL" not in message


@pytest.mark.asyncio
async def test_redirect_without_location_mid_chain_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The malformed hop is named, not the URL the caller passed in."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start.png":
            return httpx.Response(302, headers={"location": "/broken.png"})
        return httpx.Response(302, headers={})

    _patch_network(monkeypatch, handler)

    with pytest.raises(ToolError) as exc_info:
        await _fetch_image_url("https://example.com/start.png")

    assert "https://example.com/broken.png" in str(exc_info.value)


@pytest.mark.asyncio
async def test_redirect_with_location_is_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A well-formed redirect chain still resolves to the final image."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final.png"})
        return httpx.Response(200, headers={"content-type": "image/png"}, content=_png_bytes())

    _patch_network(monkeypatch, handler)

    data, mime = await _fetch_image_url("https://example.com/start")
    assert mime == "image/png"
    assert data == _png_bytes()


@pytest.mark.asyncio
async def test_redirect_chain_beyond_the_budget_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One hop past `_MAX_REDIRECTS` stops with the hop-budget error."""

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        hop = len(seen)
        return httpx.Response(302, headers={"location": f"/hop{hop}.png"})

    _patch_network(monkeypatch, handler)

    with pytest.raises(ToolError) as exc_info:
        await _fetch_image_url("https://example.com/start.png")

    assert f"Too many redirects (>{_MAX_REDIRECTS})" in str(exc_info.value)
    assert len(seen) == _MAX_REDIRECTS + 1


@pytest.mark.asyncio
async def test_last_allowed_redirect_still_delivers_the_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hop budget is off-by-one free: `_MAX_REDIRECTS` hops still succeed."""

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if len(seen) <= _MAX_REDIRECTS:
            return httpx.Response(302, headers={"location": f"/hop{len(seen)}.png"})
        return httpx.Response(200, headers={"content-type": "image/png"}, content=_png_bytes())

    _patch_network(monkeypatch, handler)

    data, mime = await _fetch_image_url("https://example.com/start.png")
    assert mime == "image/png"
    assert data == _png_bytes()
