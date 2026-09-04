"""``retry_request`` transient-failure coverage at the utility boundary.

The adapter-level suites (``test_slack_retry``, ``test_telegram_retry``)
exercise the retry helper through a channel; these tests pin the helper's
own contract — which exceptions count as transient, how a server-supplied
``Retry-After`` is resolved, and what an exhausted rate-limit returns.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from agentos.channels._util import MAX_RETRY_AFTER_S, retry_request

_REQUEST = httpx.Request("POST", "https://channel.test/api")


def _resp(status_code: int = 200, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status_code, json={"ok": True}, headers=headers, request=_REQUEST)


def _caller(*results: Any) -> AsyncMock:
    """An awaitable request callable that yields ``results`` in order."""

    async def _call(*args: Any, **kwargs: Any) -> httpx.Response:
        outcome = pending.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    pending = list(results)
    return AsyncMock(side_effect=_call)


@pytest.fixture
def sleeps() -> Any:
    """Collapse the backoff and record every delay ``retry_request`` slept."""
    with patch("agentos.channels._util.asyncio.sleep", new=AsyncMock()) as sleep:
        yield sleep


def _slept(sleeps: Any) -> list[float]:
    return [call.args[0] for call in sleeps.await_args_list]


# ---------------------------------------------------------------------------
# Transient transport errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("refused"),
        httpx.ConnectTimeout("dns/tls handshake timed out"),
        httpx.ReadTimeout("slow response"),
        httpx.WriteTimeout("slow upload"),
        httpx.PoolTimeout("no free connection"),
    ],
    ids=["connect_error", "connect_timeout", "read_timeout", "write_timeout", "pool_timeout"],
)
async def test_transient_transport_errors_are_retried(exc: Exception, sleeps: Any) -> None:
    """Every transport error that can resolve on a second try is retried.

    ``ConnectTimeout``/``WriteTimeout``/``PoolTimeout`` descend from
    ``TimeoutException``, not from ``ConnectError`` — catching only the
    latter let a DNS or TLS timeout crash the caller without any backoff.
    """
    func = _caller(exc, _resp(200))

    resp = await retry_request(func)

    assert resp.status_code == 200
    assert func.await_count == 2


@pytest.mark.parametrize(
    "exc",
    [httpx.ConnectError("refused"), httpx.ConnectTimeout("timed out"), httpx.PoolTimeout("busy")],
    ids=["connect_error", "connect_timeout", "pool_timeout"],
)
async def test_transport_error_is_reraised_once_retries_are_exhausted(
    exc: Exception, sleeps: Any
) -> None:
    func = _caller(*[exc] * 4)

    with pytest.raises(type(exc)):
        await retry_request(func, max_retries=3)

    assert func.await_count == 4  # initial attempt + 3 retries


async def test_non_transient_transport_error_is_not_retried(sleeps: Any) -> None:
    """A protocol violation is not going to fix itself — surface it at once."""
    func = _caller(httpx.UnsupportedProtocol("no scheme"))

    with pytest.raises(httpx.UnsupportedProtocol):
        await retry_request(func)

    assert func.await_count == 1
    assert _slept(sleeps) == []


# ---------------------------------------------------------------------------
# Retry-After resolution
# ---------------------------------------------------------------------------


async def test_numeric_retry_after_is_honoured(sleeps: Any) -> None:
    func = _caller(_resp(429, {"Retry-After": "2"}), _resp(200))

    resp = await retry_request(func)

    assert resp.status_code == 200
    assert _slept(sleeps) == [2.0]


async def test_http_date_retry_after_is_resolved_to_a_delay(sleeps: Any) -> None:
    """RFC 7231 §7.1.3 permits an HTTP-date; it must not crash the loop."""
    when = datetime.now(UTC) + timedelta(seconds=30)
    func = _caller(_resp(429, {"Retry-After": format_datetime(when)}), _resp(200))

    resp = await retry_request(func)

    assert resp.status_code == 200
    assert len(_slept(sleeps)) == 1
    assert 25.0 <= _slept(sleeps)[0] <= 31.0


@pytest.mark.parametrize(
    "header",
    [
        "Fri, 99 Xyz 2026 07:28:00 GMT",
        "2026-10-21T07:28:00Z",
        "soon",
        "",
        "   ",
        "nan",
        "inf",
        "-5",
    ],
    ids=["malformed_date", "iso_8601", "word", "empty", "blank", "nan", "inf", "negative"],
)
async def test_unusable_retry_after_falls_back_to_backoff(header: str, sleeps: Any) -> None:
    """An unparseable, non-finite or negative header must not raise."""
    func = _caller(_resp(429, {"Retry-After": header}), _resp(200))

    resp = await retry_request(func, base_delay=1.0)

    assert resp.status_code == 200
    assert _slept(sleeps) == [1.0]  # base_delay * 2**0


async def test_past_http_date_retry_after_falls_back_to_backoff(sleeps: Any) -> None:
    func = _caller(
        _resp(429, {"Retry-After": format_datetime(datetime.now(UTC) - timedelta(hours=1))}),
        _resp(200),
    )

    await retry_request(func, base_delay=1.0)

    assert _slept(sleeps) == [1.0]


async def test_missing_retry_after_falls_back_to_backoff(sleeps: Any) -> None:
    func = _caller(_resp(429), _resp(429), _resp(200))

    await retry_request(func, base_delay=1.0)

    assert _slept(sleeps) == [1.0, 2.0]  # base_delay * 2**attempt


@pytest.mark.parametrize(
    "header",
    ["86400", "Wed, 21 Oct 2093 07:28:00 GMT"],
    ids=["seconds", "http_date"],
)
async def test_oversized_retry_after_is_clamped(header: str, sleeps: Any) -> None:
    """A provider cannot park a channel send for hours."""
    func = _caller(_resp(429, {"Retry-After": header}), _resp(200))

    await retry_request(func)

    assert _slept(sleeps) == [MAX_RETRY_AFTER_S]


# ---------------------------------------------------------------------------
# Status handling
# ---------------------------------------------------------------------------


async def test_exhausted_rate_limit_returns_the_response(sleeps: Any) -> None:
    """The last 429 is handed back — status, headers and body intact.

    Without the ``attempt < max_retries`` guard the loop slept on the final
    attempt and then fell out into ``RuntimeError("retry_request exhausted")``,
    discarding the provider's error payload.
    """
    func = _caller(*[_resp(429, {"Retry-After": "1"})] * 3)

    resp = await retry_request(func, max_retries=2)

    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "1"
    assert func.await_count == 3
    assert _slept(sleeps) == [1.0, 1.0]  # no pointless sleep on the last attempt


async def test_exhausted_server_error_returns_the_response(sleeps: Any) -> None:
    func = _caller(*[_resp(503)] * 3)

    resp = await retry_request(func, max_retries=2)

    assert resp.status_code == 503
    assert func.await_count == 3


async def test_client_error_is_returned_without_retrying(sleeps: Any) -> None:
    func = _caller(_resp(401))

    resp = await retry_request(func)

    assert resp.status_code == 401
    assert func.await_count == 1
    assert _slept(sleeps) == []


async def test_success_passes_through_arguments(sleeps: Any) -> None:
    func = _caller(_resp(200))

    resp = await retry_request(func, "https://channel.test/api", json={"a": 1})

    assert resp.status_code == 200
    func.assert_awaited_once_with("https://channel.test/api", json={"a": 1})
