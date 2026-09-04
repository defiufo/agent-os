"""The metadata floor must hold on *both* SSRF guards, not just the loose one.

``assert_not_metadata_endpoint`` is the permissive guard: ``http_request`` is
pointed at localhost and LAN services on purpose, so blocking cloud metadata is
all it can enforce. ``assert_address_allowed_for_fetch`` is the strict guard used
by ``web_fetch``, the media image fetch, browser navigation and skill-dependency
downloads — tools that only ever have business on the public internet.

Strict must therefore be a superset of permissive. It was not: the strict guard
derived its metadata coverage from the private/link-local ranges instead of from
``_METADATA_ADDRESSES``, so Alibaba Cloud's ``100.100.100.200`` — CGNAT space,
which is neither private, loopback, link-local nor reserved — was allowed by the
strict guard while the permissive one blocked it.

The parametrized test below is the invariant that keeps the two ordered for every
address in the shared set, including any added later.
"""

from __future__ import annotations

import ipaddress
import socket

import pytest

from agentos.tools import ssrf
from agentos.tools.ssrf_client import validate_fetch_address, validate_metadata_only_address
from agentos.tools.types import SSRFBlockedError

_METADATA_ADDRESSES = sorted(ssrf._METADATA_ADDRESSES, key=str)
_METADATA_HOSTNAMES = sorted(ssrf._METADATA_HOSTNAMES)


def _fake_getaddrinfo(ip: str):
    def resolver(hostname: str, port: int | None, *args, **kwargs):
        family = socket.AF_INET6 if ipaddress.ip_address(ip).version == 6 else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (ip, port or 443))]

    return resolver


@pytest.fixture(autouse=True)
def reset_trusted_fake_ip_cidrs():
    ssrf.configure_trusted_fake_ip_cidrs([])
    yield
    ssrf.configure_trusted_fake_ip_cidrs([])


@pytest.mark.parametrize("addr", _METADATA_ADDRESSES, ids=str)
def test_every_metadata_address_is_blocked_by_both_guards(addr):
    """Neither guard may hand an agent an instance-credential endpoint."""
    with pytest.raises(SSRFBlockedError):
        ssrf.assert_address_not_metadata("metadata.test", addr)
    with pytest.raises(SSRFBlockedError):
        ssrf.assert_address_allowed_for_fetch("metadata.test", addr, ())


@pytest.mark.parametrize("addr", _METADATA_ADDRESSES, ids=str)
def test_every_metadata_address_is_blocked_at_connect_time(addr):
    """The connect-time guard shares the policy, so it must agree with both."""
    with pytest.raises(SSRFBlockedError):
        validate_metadata_only_address("metadata.test", addr)
    with pytest.raises(SSRFBlockedError):
        validate_fetch_address("metadata.test", addr)


@pytest.mark.parametrize("addr", _METADATA_ADDRESSES, ids=str)
def test_metadata_address_is_blocked_through_url_validation(addr, monkeypatch):
    """A hostname that resolves to a metadata address is refused for fetch."""
    monkeypatch.setattr(ssrf.socket, "getaddrinfo", _fake_getaddrinfo(str(addr)))

    with pytest.raises(SSRFBlockedError):
        ssrf.validate_http_url_for_fetch("https://attacker.test/steal")


@pytest.mark.parametrize("addr", _METADATA_ADDRESSES, ids=str)
def test_metadata_address_survives_trusted_fake_ip_config(addr, monkeypatch):
    """The fake-IP escape hatch must not reopen the metadata floor.

    ``trusted_fake_ip_cidrs`` is validated to RFC2544 subnets only, so it can
    never name a metadata address directly — but the floor is checked before the
    trusted-network short circuit either way.
    """
    monkeypatch.setattr(ssrf.socket, "getaddrinfo", _fake_getaddrinfo(str(addr)))

    with pytest.raises(SSRFBlockedError):
        ssrf.validate_http_url_for_fetch(
            "https://attacker.test/steal",
            trusted_fake_ip_cidrs=["198.18.0.0/15"],
        )


def test_alibaba_metadata_address_is_blocked_for_fetch():
    """Regression: CGNAT space is not private, so only the shared set catches it."""
    addr = ipaddress.ip_address("100.100.100.200")

    assert not addr.is_private
    assert not addr.is_loopback
    assert not addr.is_link_local
    assert not addr.is_reserved

    with pytest.raises(SSRFBlockedError):
        ssrf.assert_address_allowed_for_fetch("alibaba.test", addr, ())


@pytest.mark.parametrize("hostname", _METADATA_HOSTNAMES)
def test_metadata_hostname_is_blocked_for_fetch(hostname, monkeypatch):
    """A metadata *name* is refused before its answer is even considered.

    A resolver that answers these at all is answering for the credential
    endpoint, so the public address it returns must not launder the request.
    """
    monkeypatch.setattr(ssrf.socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))

    with pytest.raises(SSRFBlockedError):
        ssrf.validate_http_url_for_fetch(f"http://{hostname}/computeMetadata/v1/")


def test_ordinary_public_address_still_allowed(monkeypatch):
    """The floor must not turn into a blanket denial."""
    monkeypatch.setattr(ssrf.socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))

    ssrf.validate_http_url_for_fetch("https://example.test/index.html")
