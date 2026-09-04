"""SSRF-guarded HTTP client construction for the MCP HTTP transports.

Every other outbound-URL path in the tree runs through ``agentos.tools.ssrf``
before it opens a socket; the SSE and Streamable HTTP transports used to build a
bare ``httpx.AsyncClient`` from ``MCPServerConfig.url`` with no check at all, so
an MCP server entry pointed at ``169.254.169.254`` reached the instance
credential endpoint.

Two things are deliberate about the guard used here:

* **The policy is metadata-only, not the full fetch policy.**
  ``validate_http_url_for_fetch`` rejects loopback, private, link-local and
  reserved ranges — but ``http://localhost:PORT/mcp`` and LAN-hosted servers are
  the normal, intended MCP configuration, so that policy would break the
  majority of real setups. ``validate_metadata_only_address`` is the same floor
  ``http_request`` takes, for the same reason: no configuration makes the
  instance-credential endpoint a legitimate destination.

* **The check happens at connect time, not once against the URL text.**
  Validating the URL and then handing it to a plain client leaves httpx to
  resolve the hostname a second time when it dials, which is a DNS-rebinding
  window: a short-TTL name can answer with a public address for the check and
  with the metadata address for the socket. ``ssrf_guarded_client`` pins the
  address that was validated as the address that gets dialed.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from agentos.env import trust_env as _trust_env
from agentos.tools.ssrf_client import ssrf_guarded_client, validate_metadata_only_address
from agentos.tools.types import UnsupportedURLSchemeError

__all__ = ["assert_supported_mcp_url", "mcp_http_client"]


def assert_supported_mcp_url(url: str) -> None:
    """Raise unless *url* is an ``http``/``https`` URL with a hostname.

    A ``file://`` or ``gopher://`` entry is not something either HTTP transport
    can serve, and letting one through would push the decision down into httpx
    (or the MCP SDK) where the failure is far less legible.
    """
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise UnsupportedURLSchemeError(f"Invalid MCP server URL: {url!r}") from exc
    if parsed.scheme not in ("http", "https"):
        raise UnsupportedURLSchemeError(
            "MCP HTTP transports only support http:// and https:// URLs; got "
            f"{parsed.scheme or 'no'} scheme"
        )
    if not parsed.hostname:
        raise UnsupportedURLSchemeError(f"Invalid MCP server URL: {url!r} has no hostname")


def mcp_http_client(url: str, **kwargs: Any) -> httpx.AsyncClient:
    """Return an ``httpx.AsyncClient`` for *url* with the metadata guard installed."""
    assert_supported_mcp_url(url)
    kwargs.setdefault("trust_env", _trust_env())
    return ssrf_guarded_client(validator=validate_metadata_only_address, **kwargs)
