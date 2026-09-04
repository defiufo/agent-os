"""The connect-time SSRF guard closes the DNS-rebinding TOCTOU (issue #516).

The URL-level guard resolved a hostname and checked the answer; httpx then
resolved the same name again when it opened the socket. A rebinding domain can
answer differently for the two lookups, so the address that was validated was
not necessarily the address that got connected. These tests pin the behaviour
that removes the window: the backend resolves once, validates what it got, and
dials a validated IP literal.
"""

from __future__ import annotations

import ipaddress
import socket
import threading

import httpcore
import httpx
import pytest

from agentos.tools import ssrf
from agentos.tools.ssrf_client import (
    ValidatingNetworkBackend,
    ssrf_guarded_client,
    validate_fetch_address,
    validate_metadata_only_address,
)
from agentos.tools.types import SSRFBlockedError

PUBLIC_IP = "93.184.216.34"
METADATA_IP = "169.254.169.254"


class _RecordingBackend(httpcore.AsyncNetworkBackend):
    """Stand-in for the real backend that records what it was asked to dial."""

    def __init__(self) -> None:
        self.connections: list[tuple[str, int]] = []
        self.streams: list[object] = []
        self.unix_paths: list[str] = []
        self.slept: list[float] = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connections.append((host, port))
        stream = object()  # the caller never touches the stream in these tests
        self.streams.append(stream)
        return stream

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        self.unix_paths.append(path)
        return object()

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


def _sequence_resolver(*ips: str):
    """Resolver that answers with a different address on each call.

    IP literals resolve to themselves, so a validated literal can still be
    dialled by the underlying backend.
    """
    calls: list[str] = []

    def resolver(host, port=None, *args, **kwargs):
        calls.append(host)
        try:
            ipaddress.ip_address(str(host).strip("[]"))
        except ValueError:
            index = min(len([c for c in calls if c == host]) - 1, len(ips) - 1)
            answer = ips[index]
        else:
            answer = str(host)
        family = socket.AF_INET6 if ipaddress.ip_address(answer).version == 6 else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (answer, port or 443))]

    resolver.calls = calls  # type: ignore[attr-defined]
    return resolver


@pytest.fixture(autouse=True)
def reset_trusted_fake_ip_cidrs():
    ssrf.configure_trusted_fake_ip_cidrs([])
    yield
    ssrf.configure_trusted_fake_ip_cidrs([])


async def test_backend_connects_to_the_address_it_validated(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _sequence_resolver(PUBLIC_IP))
    inner = _RecordingBackend()

    await ValidatingNetworkBackend(validate_fetch_address, inner).connect_tcp("example.com", 443)

    # An IP literal, not the hostname: nothing downstream re-resolves the name.
    assert inner.connections == [(PUBLIC_IP, 443)]


async def test_backend_hands_back_the_wrapped_stream_untouched(monkeypatch) -> None:
    """TLS stays the wrapped backend's business.

    The stream is returned as-is, so ``start_tls`` is httpcore's own call with
    ``server_hostname`` taken from the request origin — SNI and certificate
    verification still run against the hostname, not the pinned address.
    """
    monkeypatch.setattr(socket, "getaddrinfo", _sequence_resolver(PUBLIC_IP))
    inner = _RecordingBackend()

    stream = await ValidatingNetworkBackend(validate_fetch_address, inner).connect_tcp(
        "example.com", 443
    )

    assert stream is inner.streams[-1]


async def test_rebinding_to_the_metadata_endpoint_is_blocked_at_connect(monkeypatch) -> None:
    """The guard's lookup says public, the connect-time lookup says metadata."""
    resolver = _sequence_resolver(PUBLIC_IP, METADATA_IP)
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    inner = _RecordingBackend()
    backend = ValidatingNetworkBackend(validate_fetch_address, inner)

    # Resolution #1 — the URL-level guard, which passes.
    ssrf.validate_http_url_for_fetch("https://rebind.example/x")

    # Resolution #2 — what the socket would have used. It never reaches one.
    with pytest.raises(SSRFBlockedError) as excinfo:
        await backend.connect_tcp("rebind.example", 443)

    assert METADATA_IP in str(excinfo.value)
    assert inner.connections == []


async def test_ip_literals_are_validated_without_resolving(monkeypatch) -> None:
    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("an IP literal must not be resolved")

    monkeypatch.setattr(socket, "getaddrinfo", explode)
    inner = _RecordingBackend()
    backend = ValidatingNetworkBackend(validate_fetch_address, inner)

    with pytest.raises(SSRFBlockedError):
        await backend.connect_tcp(METADATA_IP, 80)
    assert inner.connections == []


async def test_metadata_hostnames_are_blocked_by_name(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _sequence_resolver(PUBLIC_IP))
    inner = _RecordingBackend()
    backend = ValidatingNetworkBackend(validate_fetch_address, inner)

    with pytest.raises(SSRFBlockedError):
        await backend.connect_tcp("metadata.google.internal", 80)
    assert inner.connections == []


async def test_metadata_only_validator_allows_private_but_not_metadata(monkeypatch) -> None:
    """http_request keeps reaching localhost; it never reaches the IMDS."""
    monkeypatch.setattr(socket, "getaddrinfo", _sequence_resolver("127.0.0.1"))
    inner = _RecordingBackend()
    backend = ValidatingNetworkBackend(validate_metadata_only_address, inner)

    await backend.connect_tcp("dev.local", 8000)
    assert inner.connections == [("127.0.0.1", 8000)]

    with pytest.raises(SSRFBlockedError):
        await backend.connect_tcp(METADATA_IP, 80)


async def test_private_addresses_are_blocked_by_the_fetch_validator(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _sequence_resolver("10.1.2.3"))
    inner = _RecordingBackend()

    with pytest.raises(SSRFBlockedError):
        await ValidatingNetworkBackend(validate_fetch_address, inner).connect_tcp(
            "intranet.example", 80
        )
    assert inner.connections == []


async def test_unresolvable_hostname_raises_a_connect_error(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise socket.gaierror("nope")

    monkeypatch.setattr(socket, "getaddrinfo", fail)

    with pytest.raises(httpcore.ConnectError):
        await ValidatingNetworkBackend(validate_fetch_address, _RecordingBackend()).connect_tcp(
            "nowhere.example", 80
        )


async def test_unix_sockets_and_sleep_delegate_to_the_wrapped_backend() -> None:
    inner = _RecordingBackend()
    backend = ValidatingNetworkBackend(validate_fetch_address, inner)

    await backend.connect_unix_socket("/tmp/sock")
    await backend.sleep(0.0)

    assert inner.unix_paths == ["/tmp/sock"]
    assert inner.slept == [0.0]


def test_guarded_client_installs_the_backend_and_keeps_httpx_defaults() -> None:
    client = ssrf_guarded_client(timeout=7.0, follow_redirects=False)

    backend = client._transport._pool._network_backend
    assert isinstance(backend, ValidatingNetworkBackend)
    assert client.timeout.connect == 7.0
    assert client.follow_redirects is False


def test_guarded_client_leaves_proxy_mounts_alone(monkeypatch) -> None:
    """A forwarding proxy resolves the destination itself; only the pool is swapped."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
    client = ssrf_guarded_client(trust_env=True)

    assert isinstance(client._transport._pool._network_backend, ValidatingNetworkBackend)
    proxy_transports = [t for pattern, t in client._mounts.items() if t is not None]
    assert proxy_transports, "expected an env proxy mount"
    for transport in proxy_transports:
        assert not isinstance(transport._pool._network_backend, ValidatingNetworkBackend)


def test_guarded_client_fails_closed_when_the_backend_cannot_be_installed(monkeypatch) -> None:
    """A stock transport with no reachable pool means httpx changed shape."""

    class _PoollessTransport(httpx.AsyncHTTPTransport):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._pool = None  # type: ignore[assignment]

    monkeypatch.setattr(httpx._client, "AsyncHTTPTransport", _PoollessTransport)

    with pytest.raises(RuntimeError, match="refusing to fetch"):
        ssrf_guarded_client()


def test_guarded_client_leaves_a_caller_supplied_transport_alone() -> None:
    """A mock/recorder transport owns its own connect path; nothing to pin."""
    transport = httpx.MockTransport(lambda request: httpx.Response(200))

    client = ssrf_guarded_client(transport=transport)

    assert client._transport is transport


class _LocalServer:
    """A minimal HTTP/1.1 responder on an ephemeral loopback port."""

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
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                    b"Content-Length: 2\r\nConnection: close\r\n\r\nok"
                )
                conn.shutdown(socket.SHUT_WR)
        except OSError:  # pragma: no cover - the socket closed under us
            pass

    def close(self) -> None:
        self._sock.close()


async def test_real_request_uses_the_first_resolution_only(monkeypatch) -> None:
    """End-to-end: a name that rebinds after the guard still reaches the checked host.

    The resolver answers ``127.0.0.1`` once and the metadata address every time
    after. On an unguarded client the second answer is what the socket would
    use; here the request completes against the address that was validated.
    """
    server = _LocalServer()
    resolver = _sequence_resolver("127.0.0.1", METADATA_IP)
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    try:
        async with ssrf_guarded_client(
            timeout=5.0, validator=validate_metadata_only_address
        ) as client:
            response = await client.get(f"http://rebind.example:{server.port}/x")
    finally:
        server.close()

    assert response.status_code == 200
    assert response.text == "ok"
    # Exactly one lookup of the rebinding name: the guard's answer is the
    # socket's answer, so there is no second resolution to poison.
    assert resolver.calls.count("rebind.example") == 1
