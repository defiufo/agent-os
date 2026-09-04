"""A real-socket test for the http_request streaming path.

Reviewer feedback on PR #509: the fake clients in the suite have a no-op
``__aexit__``, so code that reads the stream *after* the client is closed
went green while every real request failed with ``httpx.ReadError``. This
module stands up a real local TCP socket, so the transport pool teardown in
``AsyncClient.__aexit__`` is exercised exactly as in production: if the
stream is iterated outside the client block, this test fails.
"""

from __future__ import annotations

import asyncio
import socket
import threading
from collections.abc import Awaitable, Callable
from typing import cast

import pytest

from agentos.sandbox.integration import configure_runtime
from agentos.sandbox.policy import SandboxSettings
from agentos.tools.builtin import web

HttpRequest = Callable[..., Awaitable[str]]


@pytest.fixture
def _real_http_stack(tmp_path):
    """Allow the real httpx transport to run (the whole point of this module)."""
    configure_runtime(
        SandboxSettings(sandbox=False, security_grading=False, allow_legacy_mode=True),
        workspace=tmp_path,
    )
    yield


def _http_request() -> HttpRequest:
    """The raw handler with the @tool/@sandboxed decorators unwrapped."""
    return cast(HttpRequest, web.http_request.__wrapped__.__wrapped__)


class _ChunkedServer:
    """A minimal HTTP/1.1 chunked responder on an ephemeral local port."""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self._sock.accept()
            with conn:
                conn.recv(65536)
                body = b"chunked-body-payload"
                header = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/plain\r\n"
                    b"Transfer-Encoding: chunked\r\n"
                    b"Connection: close\r\n\r\n"
                )
                conn.sendall(header)
                for i in range(0, len(body), 8):
                    part = body[i : i + 8]
                    conn.sendall(f"{len(part):x}\r\n".encode() + part + b"\r\n")
                conn.sendall(b"0\r\n\r\n")
                conn.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    def close(self) -> None:
        self._sock.close()


@pytest.mark.asyncio
async def test_streaming_read_works_with_real_httpx_transport(_real_http_stack) -> None:
    """The download must be read while the client is still open.

    Regression for the PR #509 review: iterating ``aiter_bytes`` after
    ``async with httpx.AsyncClient`` exits raises ``httpx.ReadError`` on a
    real transport (the pool is closed). A fake client with a no-op
    ``__aexit__`` cannot catch this, hence the local socket.
    """
    server = _ChunkedServer()
    try:
        result = await _http_request()(url=f"http://127.0.0.1:{server.port}/x")
    finally:
        server.close()

    assert "chunked-body-payload" in result


@pytest.mark.asyncio
async def test_streaming_read_survives_server_closing_early(_real_http_stack) -> None:
    """A truncated chunked response must not hang or crash the tool.

    The server sends one chunk header + partial body then drops the
    connection. http_request should catch the protocol error, flag the body
    as truncated, and return a well-formed JSON envelope instead of raising.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve_and_cut() -> None:
        try:
            conn, _ = srv.accept()
            with conn:
                conn.recv(65536)
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/plain\r\n"
                    b"Transfer-Encoding: chunked\r\n\r\n"
                    b"a\r\n0123456789\r\n"
                )
                # No terminator: the connection just drops.
        except OSError:
            pass

    t = threading.Thread(target=serve_and_cut, daemon=True)
    t.start()
    try:
        result = await asyncio.wait_for(
            _http_request()(url=f"http://127.0.0.1:{port}/x"),
            timeout=10,
        )
    finally:
        srv.close()
        t.join(timeout=1)

    import json

    payload = json.loads(result)
    assert payload["status"] == 200
    assert payload["body_base64_truncated"] is True
    assert "0123456789" not in payload["body_base64"]
