"""Webhook delivery mode.

``DeliveryMode.WEBHOOK`` POSTs the finished-run event payload to
``DeliveryConfig.webhook_url``, optionally with a bearer token. URL is
validated up front and rejected at add time when malformed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from agentos.scheduler.delivery import DeliveryChain, validate_webhook_url
from agentos.scheduler.ops import SchedulerOps
from agentos.scheduler.payloads import make_agent_turn_payload
from agentos.scheduler.persistence import JobStore
from agentos.scheduler.types import (
    CronJob,
    DeliveryConfig,
    DeliveryMode,
    ScheduleKind,
    SessionTarget,
)

# --- URL validation --------------------------------------------------------


def test_validate_webhook_url_accepts_http_and_https() -> None:
    validate_webhook_url("http://example.com/hook")
    validate_webhook_url("https://example.com/hook?x=1")


def test_validate_webhook_url_rejects_other_schemes() -> None:
    with pytest.raises(ValueError, match="http or https"):
        validate_webhook_url("ftp://example.com/x")
    with pytest.raises(ValueError, match="http or https"):
        validate_webhook_url("file:///tmp/x")


def test_validate_webhook_url_requires_hostname() -> None:
    with pytest.raises(ValueError, match="hostname"):
        validate_webhook_url("https:///nohost")


def test_validate_webhook_url_rejects_empty() -> None:
    with pytest.raises(ValueError, match="required"):
        validate_webhook_url("")


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.169.254/",
        "https://169.254.169.253/",
        "http://169.254.170.2/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://metadata.goog/",
    ],
)
def test_validate_webhook_url_rejects_cloud_metadata(url: str) -> None:
    """Cron webhooks may target localhost; they may not target IMDS."""
    with pytest.raises(ValueError, match="metadata"):
        validate_webhook_url(url)


def test_validate_webhook_url_still_allows_localhost() -> None:
    validate_webhook_url("http://127.0.0.1:5678/webhook")
    validate_webhook_url("http://localhost:8080/hook")


# --- ops.add validates webhook config -------------------------------------


async def test_ops_add_with_webhook_delivery_persists(tmp_path: Path) -> None:
    db = tmp_path / "cron.db"
    store = JobStore(str(db))
    await store.open()
    try:
        ops = SchedulerOps(store)
        delivery = DeliveryConfig(
            mode=DeliveryMode.WEBHOOK,
            webhook_url="https://hooks.example/cron",
            webhook_token="secret-bearer",
            best_effort=True,
        )
        job = await ops.add(
            name="hook",
            schedule_kind=ScheduleKind.CRON,
            schedule_value="*/5 * * * *",
            handler_key="agent_run",
            payload=make_agent_turn_payload("brief"),
            session_target=SessionTarget.ISOLATED,
            delivery=delivery,
        )
        assert job.delivery.mode == DeliveryMode.WEBHOOK
        assert job.delivery.webhook_url == "https://hooks.example/cron"
        assert job.delivery.webhook_token == "secret-bearer"
        assert job.delivery.best_effort is True

        reloaded = await store.get(job.id)
        assert reloaded is not None
        assert reloaded.delivery.mode == DeliveryMode.WEBHOOK
        assert reloaded.delivery.webhook_url == "https://hooks.example/cron"
        assert reloaded.delivery.webhook_token == "secret-bearer"
        assert reloaded.delivery.best_effort is True
    finally:
        await store.close()


async def test_ops_add_rejects_webhook_without_url(tmp_path: Path) -> None:
    db = tmp_path / "cron.db"
    store = JobStore(str(db))
    await store.open()
    try:
        ops = SchedulerOps(store)
        with pytest.raises(ValueError, match="webhook URL is required"):
            await ops.add(
                name="bad",
                schedule_kind=ScheduleKind.CRON,
                schedule_value="*/5 * * * *",
                handler_key="agent_run",
                payload=make_agent_turn_payload("x"),
                session_target=SessionTarget.ISOLATED,
                delivery=DeliveryConfig(mode=DeliveryMode.WEBHOOK, webhook_url=""),
            )
    finally:
        await store.close()


async def test_ops_add_rejects_metadata_webhook_url(tmp_path: Path) -> None:
    db = tmp_path / "cron.db"
    store = JobStore(str(db))
    await store.open()
    try:
        ops = SchedulerOps(store)
        with pytest.raises(ValueError, match="metadata"):
            await ops.add(
                name="imds",
                schedule_kind=ScheduleKind.CRON,
                schedule_value="*/5 * * * *",
                handler_key="agent_run",
                payload=make_agent_turn_payload("x"),
                session_target=SessionTarget.ISOLATED,
                delivery=DeliveryConfig(
                    mode=DeliveryMode.WEBHOOK,
                    webhook_url="http://169.254.169.254/latest/meta-data/",
                ),
            )
    finally:
        await store.close()


async def test_ops_add_rejects_webhook_with_bad_scheme(tmp_path: Path) -> None:
    db = tmp_path / "cron.db"
    store = JobStore(str(db))
    await store.open()
    try:
        ops = SchedulerOps(store)
        with pytest.raises(ValueError, match="http or https"):
            await ops.add(
                name="bad",
                schedule_kind=ScheduleKind.CRON,
                schedule_value="*/5 * * * *",
                handler_key="agent_run",
                payload=make_agent_turn_payload("x"),
                session_target=SessionTarget.ISOLATED,
                delivery=DeliveryConfig(
                    mode=DeliveryMode.WEBHOOK, webhook_url="ftp://example.com/x"
                ),
            )
    finally:
        await store.close()


async def test_ops_add_allows_webhook_on_main_target(tmp_path: Path) -> None:
    """Webhook delivery is permitted for any sessionTarget, including main."""
    db = tmp_path / "cron.db"
    store = JobStore(str(db))
    await store.open()
    try:
        from agentos.scheduler.payloads import make_system_event_payload

        ops = SchedulerOps(store)
        job = await ops.add(
            name="main-hook",
            schedule_kind=ScheduleKind.CRON,
            schedule_value="*/5 * * * *",
            handler_key="system_event",
            payload=make_system_event_payload("reminder"),
            session_target=SessionTarget.MAIN,
            delivery=DeliveryConfig(
                mode=DeliveryMode.WEBHOOK,
                webhook_url="https://hooks.example/main",
            ),
        )
        assert job.delivery.mode == DeliveryMode.WEBHOOK
        reloaded = await store.get(job.id)
        assert reloaded is not None
        assert reloaded.delivery.mode == DeliveryMode.WEBHOOK
        assert reloaded.delivery.webhook_url == "https://hooks.example/main"
    finally:
        await store.close()


# --- DeliveryChain webhook dispatch ---------------------------------------


def _webhook_job(url: str, token: str = "") -> CronJob:
    return CronJob(
        id="job-1",
        name="hook",
        cron_expr="*/5 * * * *",
        handler_key="agent_run",
        payload={"kind": "agent_turn", "task": "x", "agent_id": "main"},
        session_target=SessionTarget.ISOLATED,
        delivery=DeliveryConfig(
            mode=DeliveryMode.WEBHOOK,
            webhook_url=url,
            webhook_token=token,
        ),
    )


class _RecordingAsyncClient:
    """Capture httpx.AsyncClient.post calls for assertion."""

    instances: list[_RecordingAsyncClient] = []

    def __init__(self, *, timeout=None, **_kw) -> None:
        self.timeout = timeout
        self.posts: list[dict] = []
        _RecordingAsyncClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        self.posts.append({"url": url, "json": json, "headers": headers or {}})

        class _Resp:
            status_code = 200

            def raise_for_status(self):
                return None

        return _Resp()


async def test_deliver_webhook_posts_json_with_bearer(monkeypatch) -> None:
    _RecordingAsyncClient.instances.clear()

    class _FakeHttpx:
        AsyncClient = _RecordingAsyncClient

    monkeypatch.setitem(__import__("sys").modules, "httpx", _FakeHttpx)

    chain = DeliveryChain()
    status = await chain._deliver_webhook(
        _webhook_job("https://hooks.example/cron", token="abc"),
        text="summary text",
    )
    assert status == "delivered"
    assert _RecordingAsyncClient.instances, "AsyncClient was not constructed"
    inst = _RecordingAsyncClient.instances[-1]
    assert inst.posts, "no POST issued"
    post = inst.posts[-1]
    assert post["url"] == "https://hooks.example/cron"
    assert post["json"]["jobId"] == "job-1"
    assert post["json"]["summary"] == "summary text"
    assert post["headers"]["Content-Type"] == "application/json"
    assert post["headers"]["Authorization"] == "Bearer abc"


async def test_deliver_webhook_omits_authorization_when_no_token(monkeypatch) -> None:
    _RecordingAsyncClient.instances.clear()

    class _FakeHttpx:
        AsyncClient = _RecordingAsyncClient

    monkeypatch.setitem(__import__("sys").modules, "httpx", _FakeHttpx)

    chain = DeliveryChain()
    status = await chain._deliver_webhook(
        _webhook_job("https://hooks.example/cron"),
        text="x",
    )
    assert status == "delivered"
    inst = _RecordingAsyncClient.instances[-1]
    assert "Authorization" not in inst.posts[-1]["headers"]


async def test_deliver_webhook_returns_failed_on_http_error(monkeypatch, no_backoff) -> None:
    class _ErrorClient(_RecordingAsyncClient):
        async def post(self, url, json=None, headers=None):
            self.posts.append({"url": url, "json": json, "headers": headers or {}})

            class _Resp:
                status_code = 500

                def raise_for_status(self):
                    raise RuntimeError("HTTP 500")

            return _Resp()

    class _FakeHttpx:
        AsyncClient = _ErrorClient

    monkeypatch.setitem(__import__("sys").modules, "httpx", _FakeHttpx)
    _RecordingAsyncClient.instances.clear()

    chain = DeliveryChain()
    status = await chain._deliver_webhook(
        _webhook_job("https://hooks.example/cron"),
        text="x",
    )
    assert status == "delivery_failed"
    # A 5xx is transient: the initial attempt plus retry_request's max_retries=3.
    assert len(_RecordingAsyncClient.instances[-1].posts) == 4


# --- transient-failure retries (issue #469) --------------------------------


@pytest.fixture
def no_backoff():
    """Collapse ``retry_request``'s sleeps so retry assertions stay fast."""
    with patch("agentos.channels._util.asyncio.sleep", new=AsyncMock()) as sleep:
        yield sleep


def _scripted_httpx(monkeypatch, responses):
    """Install a fake ``httpx`` whose POSTs replay ``responses`` in order."""

    class _ScriptedClient(_RecordingAsyncClient):
        async def post(self, url, json=None, headers=None):
            self.posts.append({"url": url, "json": json, "headers": headers or {}})
            item = responses[min(len(self.posts) - 1, len(responses) - 1)]
            if isinstance(item, Exception):
                raise item
            return item

    class _FakeHttpx:
        AsyncClient = _ScriptedClient

    monkeypatch.setitem(__import__("sys").modules, "httpx", _FakeHttpx)
    _RecordingAsyncClient.instances.clear()


def _webhook_response(status_code: int, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code,
        headers=headers,
        request=httpx.Request("POST", "https://hooks.example/cron"),
    )


async def test_deliver_webhook_retries_transient_5xx_then_succeeds(monkeypatch, no_backoff) -> None:
    _scripted_httpx(monkeypatch, [_webhook_response(503), _webhook_response(200)])

    chain = DeliveryChain()
    status = await chain._deliver_webhook(
        _webhook_job("https://hooks.example/cron"),
        text="x",
    )

    assert status == "delivered"
    assert len(_RecordingAsyncClient.instances[-1].posts) == 2


async def test_deliver_webhook_retries_connect_error_then_succeeds(monkeypatch, no_backoff) -> None:
    _scripted_httpx(monkeypatch, [httpx.ConnectError("refused"), _webhook_response(200)])

    chain = DeliveryChain()
    status = await chain._deliver_webhook(
        _webhook_job("https://hooks.example/cron"),
        text="x",
    )

    assert status == "delivered"
    assert len(_RecordingAsyncClient.instances[-1].posts) == 2


async def test_deliver_webhook_does_not_retry_fatal_status(monkeypatch, no_backoff) -> None:
    """A 400/401 is the receiver's verdict, not a blip — fail on the first try."""
    for status_code in (400, 401):
        _scripted_httpx(monkeypatch, [_webhook_response(status_code)])

        chain = DeliveryChain()
        status = await chain._deliver_webhook(
            _webhook_job("https://hooks.example/cron", token="abc"),
            text="x",
        )

        assert status == "delivery_failed"
        assert len(_RecordingAsyncClient.instances[-1].posts) == 1
    no_backoff.assert_not_awaited()


async def test_deliver_webhook_honours_retry_after_on_429(monkeypatch, no_backoff) -> None:
    _scripted_httpx(
        monkeypatch,
        [_webhook_response(429, {"Retry-After": "2"}), _webhook_response(200)],
    )

    chain = DeliveryChain()
    status = await chain._deliver_webhook(
        _webhook_job("https://hooks.example/cron"),
        text="x",
    )

    assert status == "delivered"
    no_backoff.assert_awaited_once_with(2.0)
