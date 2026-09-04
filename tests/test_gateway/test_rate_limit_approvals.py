"""GET /api/approvals is bounded, but in its own per-IP bucket (#569).

The endpoint used to be exempt from rate limiting altogether, so anyone who
could reach the gateway could loop it to enumerate every pending exec/plugin
approval (command, argv, params) and to hold the SQLite read lock against the
approval/chat pipeline. It is now counted — in a dedicated bucket, because the
Control UI polls it every 1.5s and charging that to the shared ``/api/*`` cap
would 429 the operator out of their own approval queue.
"""

from __future__ import annotations

import re
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from agentos.gateway.config import GatewayConfig, RateLimitConfig
from agentos.gateway.middleware import _APPROVALS_PATH, RateLimitMiddleware

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APPROVAL_MONITOR_TS = _REPO_ROOT / "frontend" / "src" / "services" / "approval-monitor.ts"


def _frontend_poll_ms() -> int:
    """ApprovalMonitor's base poll delay, read from the TS source.

    Read rather than duplicated so that lowering ``POLL_MS`` in the frontend
    actually moves the drift guard below instead of leaving it asserting
    against a stale copy of the number.
    """
    match = re.search(r"^const POLL_MS = (\d+)$", _APPROVAL_MONITOR_TS.read_text(), re.MULTILINE)
    assert match is not None, f"POLL_MS declaration not found in {_APPROVAL_MONITOR_TS}"
    return int(match.group(1))


def _build_app(
    *,
    max_requests: int = 100,
    approvals_max_requests: int = 300,
    window_seconds: int = 60,
) -> Starlette:
    app = Starlette()

    async def approvals(_request: Request) -> JSONResponse:
        return JSONResponse({"approvals": []})

    async def sessions(_request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    async def resolve(_request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    app.add_route("/api/approvals", approvals, methods=["GET"])
    app.add_route("/api/sessions", sessions, methods=["GET"])
    app.add_route("/api/approvals/resolve", resolve, methods=["POST"])

    config = GatewayConfig(
        rate_limit=RateLimitConfig(
            enabled=True,
            max_requests=max_requests,
            approvals_max_requests=approvals_max_requests,
            window_seconds=window_seconds,
        )
    )
    app.add_middleware(RateLimitMiddleware, config=config)
    return app


def test_approval_polling_is_bounded() -> None:
    """The enumeration lever from #569: the endpoint can no longer be sprayed."""
    app = _build_app(approvals_max_requests=2)

    with TestClient(app) as client:
        assert client.get("/api/approvals").status_code == 200
        assert client.get("/api/approvals").status_code == 200
        resp = client.get("/api/approvals")
        assert resp.status_code == 429
        assert resp.json() == {"error": "Too Many Requests", "code": "RATE_LIMITED"}


def test_approval_polling_does_not_consume_generic_api_rate_limit() -> None:
    """The UI poll must not spend the budget the rest of /api/* runs on."""
    app = _build_app(max_requests=1, approvals_max_requests=10)

    with TestClient(app) as client:
        for _ in range(5):
            assert client.get("/api/approvals").status_code == 200
        # The shared bucket is untouched: /api/sessions still has its one slot.
        assert client.get("/api/sessions").status_code == 200
        assert client.get("/api/sessions").status_code == 429


def test_generic_api_traffic_does_not_exhaust_the_approvals_bucket() -> None:
    """The reverse: busy REST traffic must not lock the operator out of approvals."""
    app = _build_app(max_requests=1, approvals_max_requests=2)

    with TestClient(app) as client:
        assert client.get("/api/sessions").status_code == 200
        assert client.get("/api/sessions").status_code == 429
        assert client.get("/api/approvals").status_code == 200
        assert client.get("/api/approvals").status_code == 200
        assert client.get("/api/approvals").status_code == 429


def test_mutating_approval_routes_use_the_shared_bucket() -> None:
    """Only the polled GET gets the roomy bucket; resolve/settings do not."""
    app = _build_app(max_requests=1, approvals_max_requests=100)

    with TestClient(app) as client:
        assert client.post("/api/approvals/resolve", json={}).status_code == 200
        assert client.post("/api/approvals/resolve", json={}).status_code == 429
        # ...and it drew from the same bucket as the rest of the API.
        assert client.get("/api/sessions").status_code == 429


def test_approvals_bucket_is_per_ip() -> None:
    """Two clients get independent approval budgets."""
    app = _build_app(approvals_max_requests=1)

    with TestClient(app, client=("198.51.100.7", 40000)) as client:
        assert client.get("/api/approvals").status_code == 200
        assert client.get("/api/approvals").status_code == 429

    with TestClient(app, client=("198.51.100.8", 40000)) as client:
        assert client.get("/api/approvals").status_code == 200


def test_disabled_rate_limit_still_bypasses_everything() -> None:
    app = Starlette()

    async def approvals(_request: Request) -> JSONResponse:
        return JSONResponse({"approvals": []})

    app.add_route("/api/approvals", approvals, methods=["GET"])
    config = GatewayConfig(
        rate_limit=RateLimitConfig(enabled=False, max_requests=1, window_seconds=60)
    )
    app.add_middleware(RateLimitMiddleware, config=config)

    with TestClient(app) as client:
        for _ in range(5):
            assert client.get("/api/approvals").status_code == 200


def test_default_approvals_bucket_leaves_room_for_several_open_tabs() -> None:
    """Drift guard: the default must not 429 a normally-polling operator.

    ApprovalMonitor polls every ``POLL_MS``; the default bucket has to clear
    that rate for a handful of open Control UI tabs with slack left over. The
    real per-tab rate runs a little above the steady-state figure — the monitor
    re-polls on focus/visibility change and after each resolve — so the margin
    here is deliberately more than the tab count implies.
    """
    defaults = RateLimitConfig()
    per_tab_per_window = (defaults.window_seconds * 1000) // _frontend_poll_ms()
    assert defaults.approvals_max_requests >= per_tab_per_window * 4
    # And it must still be a bound, not an exemption.
    assert defaults.approvals_max_requests > defaults.max_requests


def test_bucket_path_matches_the_registered_approvals_route() -> None:
    """The dedicated bucket hangs on an exact path match — pin it to the route.

    ``_bucket_for`` compares against ``_APPROVALS_PATH`` literally. If the
    route in ``gateway/app.py`` is ever renamed or re-prefixed, the match
    silently stops firing and the UI poll drops back into the shared bucket,
    429-ing the operator out of the approval queue at ~2.5 open tabs — with
    every other test in this file still green.
    """
    from agentos.gateway import app as gateway_app

    source = Path(gateway_app.__file__).read_text()
    assert f'Route("{_APPROVALS_PATH}", api_approvals, methods=["GET"])' in source, (
        f"the approvals route no longer matches _APPROVALS_PATH ({_APPROVALS_PATH}); "
        "update middleware._APPROVALS_PATH to match gateway/app.py"
    )


def test_head_on_approvals_uses_the_approvals_bucket() -> None:
    """HEAD runs the same handler as GET, so it must draw on the same bucket.

    Starlette serves HEAD from a ``methods=["GET"]`` route, so a HEAD does the
    full pending-approval serialization and SQLite read. Charging it to the
    shared bucket would leave the handler reachable ``max_requests`` extra
    times per window on top of the approvals cap.
    """
    app = _build_app(max_requests=5, approvals_max_requests=1)

    with TestClient(app) as client:
        assert client.head("/api/approvals").status_code == 200
        assert client.head("/api/approvals").status_code == 429
        # It spent the approvals budget, not the shared one.
        assert client.get("/api/approvals").status_code == 429
        assert client.get("/api/sessions").status_code == 200
