from __future__ import annotations

import asyncio
import json

import pytest

from agentos.mcp.stdio import MCPStdioClient
from agentos.mcp.types import MCPServerConfig


class _FakeProcess:
    def __init__(self, *, exits_on_terminate: bool = True) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.wait_calls = 0
        self.exits_on_terminate = exits_on_terminate

    def terminate(self) -> None:
        self.terminated = True
        if self.exits_on_terminate:
            self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        self.wait_calls += 1
        if self.returncode is None:
            await asyncio.sleep(3600)
        return self.returncode


def _client_with_process(process: _FakeProcess) -> MCPStdioClient:
    client = MCPStdioClient(MCPServerConfig(name="demo", transport="stdio", command="demo"))
    client._process = process  # type: ignore[assignment]
    return client


@pytest.mark.asyncio
async def test_close_waits_for_terminated_stdio_process() -> None:
    process = _FakeProcess(exits_on_terminate=True)

    await _client_with_process(process).close()

    assert process.terminated is True
    assert process.killed is False
    assert process.wait_calls == 1


@pytest.mark.asyncio
async def test_close_kills_stdio_process_when_terminate_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess(exits_on_terminate=False)
    client = _client_with_process(process)
    monkeypatch.setattr(client, "_CLOSE_TIMEOUT_SECONDS", 0.001)

    await client.close()

    assert process.terminated is True
    assert process.killed is True
    assert process.wait_calls == 2


class _StdoutOnlyProcess:
    """Minimal process stub exposing only a ``stdout`` StreamReader."""

    def __init__(self, stdout: asyncio.StreamReader) -> None:
        self.stdout = stdout
        self.stdin = None


def _framed(payload: bytes) -> bytes:
    return f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload


@pytest.mark.asyncio
async def test_read_response_handles_body_split_across_reads() -> None:
    """A body delivered in multiple pipe writes must not be truncated.

    ``StreamReader.read(n)`` returns as soon as *any* data is buffered, so the
    pre-fix code truncated bodies that did not arrive in a single read and then
    failed ``json.loads``. The fix uses ``readexactly`` to honor the
    Content-Length frame.
    """
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "x"} for _ in range(50)]}}
    ).encode()
    frame = _framed(payload)
    # Split mid-body so the first read cannot see the whole payload.
    split = len(frame) - (len(payload) // 2)

    reader = asyncio.StreamReader()
    process = _StdoutOnlyProcess(reader)
    client = MCPStdioClient(MCPServerConfig(name="demo", transport="stdio", command="demo"))
    client._process = process  # type: ignore[assignment]

    async def feed() -> None:
        reader.feed_data(frame[:split])
        await asyncio.sleep(0)  # force a scheduler yield between chunks
        reader.feed_data(frame[split:])
        reader.feed_eof()

    feeder = asyncio.create_task(feed())
    response = await client._read_response()
    await feeder

    assert response["id"] == 1
    assert len(response["result"]["tools"]) == 50


@pytest.mark.asyncio
async def test_read_response_raises_on_truncated_body() -> None:
    """Premature EOF must surface as a clear ValueError, not a JSON error."""
    payload = b'{"jsonrpc":"2.0","id":1,"result":{}}'
    frame = _framed(payload)[:-5]  # drop the tail so EOF arrives early

    reader = asyncio.StreamReader()
    reader.feed_data(frame)
    reader.feed_eof()
    process = _StdoutOnlyProcess(reader)
    client = MCPStdioClient(MCPServerConfig(name="demo", transport="stdio", command="demo"))
    client._process = process  # type: ignore[assignment]

    with pytest.raises(ValueError, match="Truncated body"):
        await client._read_response()
