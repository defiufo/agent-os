"""X (Twitter) search built-in tool backed by xAI's server-side ``x_search``.

Adapted from NousResearch/hermes-agent ``tools/x_search_tool.py``
(MIT, Copyright (c) 2025 Nous Research) — see ``THIRD_PARTY_NOTICES.md``.

What this is
------------
Not a search provider. ``agentos.search`` providers return a list of
``(title, url, snippet)`` rows; xAI's ``x_search`` runs the search server-side
on X's post index and returns a *synthesized answer* plus citations. The two
shapes do not compose, so this stays a distinct tool rather than a
``web_search`` backend.

Credentials
-----------
Two paths, and SuperGrok / X Premium+ OAuth wins when both are present — it
spends a subscription the user already pays for instead of API credit:

1. OAuth via ``agentos auth login xai`` (``credential_source: "xai-oauth"``)
2. ``XAI_API_KEY``, or a pasted key in config (``credential_source: "xai"``)

With neither, the tool is hidden from the model's schema entirely — see
:func:`x_search_available` and ``tools.policy_runtime``.

Defensive output
----------------
Two signals beyond xAI's raw response let a caller tell a citation-backed
answer from an unsourced one:

* ``from_date`` / ``to_date`` are validated before the HTTP call. Malformed,
  inverted, and pure-future ranges fail fast instead of burning a billable
  call that is guaranteed to return nothing.
* ``degraded`` is ``True`` when a narrowing filter was active AND xAI returned
  no citations in either channel. The answer then came from the model's own
  training data and must not be treated as an X result.

Timeouts
--------
Hermes runs this synchronously with no outer deadline. AgentOS caps every tool
call (``EngineConfig.tool_timeout``, 60s by default), so the retry loop here is
deadline-aware: ``timeout_seconds`` bounds one attempt, ``total_timeout_seconds``
bounds the whole call, and the tool declares a static ceiling above both so the
engine never cuts an attempt the tool still considers live.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, date, datetime
from typing import Any

import httpx
import structlog

from agentos.env import trust_env as _trust_env
from agentos.sandbox.integration import sandboxed
from agentos.tools.registry import tool
from agentos.tools.ssrf import assert_not_metadata_endpoint
from agentos.tools.ssrf_client import ssrf_guarded_client, validate_metadata_only_address

log = structlog.get_logger(__name__)

DEFAULT_X_SEARCH_MODEL = "grok-4.5"
DEFAULT_X_SEARCH_BASE_URL = "https://api.x.ai/v1"
DEFAULT_X_SEARCH_API_KEY_ENV = "XAI_API_KEY"
DEFAULT_X_SEARCH_TIMEOUT_SECONDS = 180.0
DEFAULT_X_SEARCH_TOTAL_TIMEOUT_SECONDS = 300.0
DEFAULT_X_SEARCH_RETRIES = 2

X_SEARCH_REASONING_EFFORTS = ("low", "medium", "high", "xhigh")
MAX_HANDLES = 10

# Ceiling handed to the engine as this tool's static execution timeout. It sits
# above the largest configurable ``total_timeout_seconds`` so the engine acts as
# a backstop for a wedged client, never as the thing that cuts a live attempt.
_ENGINE_TIMEOUT_CEILING_SECONDS = 620.0
_MAX_TOTAL_TIMEOUT_SECONDS = 600.0
_MIN_ATTEMPT_TIMEOUT_SECONDS = 30.0
_MAX_BACKOFF_SECONDS = 5.0

_USER_AGENT = "AgentOS/x_search"


# ---------------------------------------------------------------------------
# Runtime configuration
# ---------------------------------------------------------------------------

_active_enabled: bool = True
_active_model: str = DEFAULT_X_SEARCH_MODEL
_active_base_url: str = DEFAULT_X_SEARCH_BASE_URL
_active_api_key: str = ""
_active_api_key_env: str = DEFAULT_X_SEARCH_API_KEY_ENV
_active_reasoning_effort: str = ""
_active_timeout_seconds: float = DEFAULT_X_SEARCH_TIMEOUT_SECONDS
_active_total_timeout_seconds: float = DEFAULT_X_SEARCH_TOTAL_TIMEOUT_SECONDS
_active_retries: int = DEFAULT_X_SEARCH_RETRIES


def _normalize_base_url(raw: str) -> str:
    """Return a usable xAI base URL, or fall back to the default.

    The value is operator-controlled rather than model-controlled, so this is
    not an SSRF boundary in the ``web_fetch`` sense. It is still worth refusing
    the instance-credential endpoint and plain HTTP: a bearer token is attached
    to every request this URL receives.
    """
    candidate = (raw or "").strip().rstrip("/")
    if not candidate:
        return DEFAULT_X_SEARCH_BASE_URL
    if not candidate.startswith("https://"):
        log.warning("x_search.base_url_rejected", reason="not_https", base_url=candidate)
        return DEFAULT_X_SEARCH_BASE_URL
    try:
        assert_not_metadata_endpoint(candidate)
    except Exception:  # noqa: BLE001 - configuration must not take the gateway down
        log.warning("x_search.base_url_rejected", reason="metadata_endpoint")
        return DEFAULT_X_SEARCH_BASE_URL
    return candidate


def configure_x_search(config: Any | None = None) -> None:
    """Apply an ``XSearchConfig``-shaped object to process-wide runtime state."""
    global _active_enabled, _active_model, _active_base_url, _active_api_key
    global _active_api_key_env, _active_reasoning_effort, _active_timeout_seconds
    global _active_total_timeout_seconds, _active_retries

    def _get(name: str, default: Any) -> Any:
        if config is None:
            return default
        value = getattr(config, name, None)
        return default if value is None else value

    _active_enabled = bool(_get("enabled", True))
    _active_model = str(_get("model", DEFAULT_X_SEARCH_MODEL)).strip() or DEFAULT_X_SEARCH_MODEL
    _active_base_url = _normalize_base_url(str(_get("base_url", DEFAULT_X_SEARCH_BASE_URL)))
    _active_api_key = str(_get("api_key", "")).strip()
    _active_api_key_env = (
        str(_get("api_key_env", DEFAULT_X_SEARCH_API_KEY_ENV)).strip()
        or DEFAULT_X_SEARCH_API_KEY_ENV
    )
    _active_reasoning_effort = str(_get("reasoning_effort", "")).strip().lower()

    timeout = float(_get("timeout_seconds", DEFAULT_X_SEARCH_TIMEOUT_SECONDS))
    _active_timeout_seconds = max(_MIN_ATTEMPT_TIMEOUT_SECONDS, timeout)
    total = float(_get("total_timeout_seconds", DEFAULT_X_SEARCH_TOTAL_TIMEOUT_SECONDS))
    _active_total_timeout_seconds = min(
        _MAX_TOTAL_TIMEOUT_SECONDS, max(_active_timeout_seconds, total)
    )
    # Retries are not clamped against the total budget here: attempts usually
    # finish well inside ``timeout_seconds``, and pre-clamping on the worst case
    # would disable retries for every sane config. The deadline in
    # :func:`_post_with_retries` is what actually bounds the call.
    _active_retries = max(0, int(_get("retries", DEFAULT_X_SEARCH_RETRIES)))


def reset_x_search_runtime() -> None:
    """Restore boot defaults. Used by tests and by a config reload to a bare state."""
    configure_x_search(None)


def _resolve_api_key() -> str:
    """Return the pasted key, else the one named by ``api_key_env``."""
    import os

    if _active_api_key:
        return _active_api_key
    env_name = _active_api_key_env or DEFAULT_X_SEARCH_API_KEY_ENV
    return os.environ.get(env_name, "").strip()


def _oauth_login_present() -> bool:
    try:
        from agentos.xai_oauth import has_oauth_credentials

        return has_oauth_credentials()
    except Exception:  # noqa: BLE001 - capability checks must never raise
        return False


async def _resolve_credential() -> tuple[str, str, str]:
    """Return ``(bearer, base_url, source)``.

    OAuth is tried first so a SuperGrok subscriber spends their subscription
    rather than API credit. A *broken* OAuth login is reported rather than
    skipped: falling through to an API key the user may not have would turn
    "your xAI login expired" into "no credentials configured".
    """
    from agentos.xai_oauth import XaiOAuthError, resolve_oauth_bearer

    try:
        resolved = await resolve_oauth_bearer()
    except XaiOAuthError:
        raise
    except Exception as exc:  # noqa: BLE001 - a store problem must not mask the key path
        log.warning("x_search.oauth_resolve_failed", error_type=type(exc).__name__)
        resolved = None

    if resolved is not None:
        access_token, oauth_base_url = resolved
        return access_token, oauth_base_url, "xai-oauth"

    api_key = _resolve_api_key()
    if api_key:
        return api_key, _active_base_url, "xai"
    raise XaiOAuthError(
        "No xAI credentials available. Run `agentos auth login xai` to use a "
        "SuperGrok / X Premium+ subscription, or set XAI_API_KEY.",
        code="xai_no_credentials",
    )


def x_search_available() -> bool:
    """Whether ``x_search`` has everything it needs to run.

    Called every time the tool surface is rebuilt, so it stays local: an
    OAuth login counts as present without validating it over the network. A
    revoked token surfaces as a call error, which is better than a tool that
    disappears from the schema partway through a session.
    """
    if not _active_enabled:
        return False
    try:
        return bool(_resolve_api_key()) or _oauth_login_present()
    except Exception:  # noqa: BLE001 - capability checks must never raise
        return False


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------


def _normalize_handles(handles: list[str] | None, field_name: str) -> list[str]:
    cleaned: list[str] = []
    for handle in handles or []:
        normalized = str(handle or "").strip().lstrip("@")
        if normalized:
            cleaned.append(normalized)
    if len(cleaned) > MAX_HANDLES:
        raise ValueError(f"{field_name} supports at most {MAX_HANDLES} handles")
    return cleaned


def _parse_iso_date(value: str, field_name: str) -> date:
    """Parse a strict ``YYYY-MM-DD`` string.

    xAI accepts any string in the date slots and silently answers with no
    citations when the value is malformed. That burns a billable call and
    produces a confident-sounding result the caller cannot distinguish from a
    real one, so the check happens here.
    """
    raw = value.strip()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC).date()
    except ValueError as exc:
        raise ValueError(f"{field_name} must be YYYY-MM-DD (got {raw!r})") from exc


def _validate_date_range(from_date: str, to_date: str) -> None:
    """Reject ranges that cannot match a post before spending an API call."""
    parsed_from = _parse_iso_date(from_date, "from_date") if from_date.strip() else None
    parsed_to = _parse_iso_date(to_date, "to_date") if to_date.strip() else None
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise ValueError(
            f"from_date ({parsed_from.isoformat()}) must be on or before "
            f"to_date ({parsed_to.isoformat()})"
        )
    if parsed_from is not None:
        today_utc = datetime.now(UTC).date()
        if parsed_from > today_utc:
            raise ValueError(
                f"from_date ({parsed_from.isoformat()}) is in the future; X Search only "
                f"indexes past posts (today UTC is {today_utc.isoformat()})"
            )
    # ``to_date`` in the future is legitimate: "from yesterday to tomorrow"
    # is how a caller asks for posts as they arrive.


def _resolve_reasoning_effort() -> str:
    if not _active_reasoning_effort:
        return ""
    if _active_reasoning_effort not in X_SEARCH_REASONING_EFFORTS:
        allowed = ", ".join(X_SEARCH_REASONING_EFFORTS)
        raise ValueError(
            f"x_search.reasoning_effort must be one of: {allowed} "
            f"(got {_active_reasoning_effort!r})"
        )
    return _active_reasoning_effort


def _extract_response_text(payload: dict[str, Any]) -> str:
    output_text = str(payload.get("output_text") or "").strip()
    if output_text:
        return output_text

    parts: list[str] = []
    for item in payload.get("output", []) or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content", []) or []:
            if content.get("type") in {"output_text", "text"}:
                text = str(content.get("text") or "").strip()
                if text:
                    parts.append(text)
    return "\n\n".join(parts).strip()


def _extract_inline_citations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for item in payload.get("output", []) or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content", []) or []:
            for annotation in content.get("annotations", []) or []:
                if annotation.get("type") != "url_citation":
                    continue
                citations.append(
                    {
                        "url": annotation.get("url", ""),
                        "title": annotation.get("title", ""),
                        "start_index": annotation.get("start_index"),
                        "end_index": annotation.get("end_index"),
                    }
                )
    return citations


def _http_error_message(exc: httpx.HTTPStatusError) -> str:
    """Summarize an xAI error body without echoing anything unbounded."""
    response = exc.response
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 - error bodies are not always JSON
        payload = None

    if isinstance(payload, dict):
        code = str(payload.get("code") or "").strip()
        error = str(payload.get("error") or "").strip()
        message = error or str(payload)
        if code and code not in message:
            message = f"{code}: {message}"
        return message[:500] or str(exc)

    text = str(getattr(response, "text", "") or "").strip()
    return text[:500] if text else str(exc)


def _build_tool_definition(
    allowed: list[str],
    excluded: list[str],
    from_date: str,
    to_date: str,
    enable_image_understanding: bool,
    enable_video_understanding: bool,
) -> dict[str, Any]:
    tool_def: dict[str, Any] = {"type": "x_search"}
    if allowed:
        tool_def["allowed_x_handles"] = allowed
    if excluded:
        tool_def["excluded_x_handles"] = excluded
    if from_date.strip():
        tool_def["from_date"] = from_date.strip()
    if to_date.strip():
        tool_def["to_date"] = to_date.strip()
    if enable_image_understanding:
        tool_def["enable_image_understanding"] = True
    if enable_video_understanding:
        tool_def["enable_video_understanding"] = True
    return tool_def


def _failure(message: str, *, error_type: str = "ToolError") -> str:
    return json.dumps(
        {
            "success": False,
            "provider": "xai",
            "tool": "x_search",
            "error": message,
            "error_type": error_type,
        },
        ensure_ascii=False,
    )


async def _post_with_retries(
    api_key: str,
    base_url: str,
    payload: dict[str, Any],
    deadline: float,
) -> httpx.Response:
    """POST to xAI, retrying transient failures inside the total budget."""
    attempt = 0
    last_exc: Exception | None = None
    # Same floor the configured base URL was checked against, re-applied to the
    # address the socket dials: a bearer token rides on every request here, so a
    # rebinding host must not be able to redirect it to the metadata endpoint.
    async with ssrf_guarded_client(
        trust_env=_trust_env(), validator=validate_metadata_only_address
    ) as client:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # `remaining` comes from `deadline`, itself a rounded float sum, so it can
            # exceed the total budget by an ulp when both `monotonic()` reads land in
            # the same clock tick (Windows granularity is ~15.6ms). Cap on the budget
            # itself so an attempt can never outlive it.
            attempt_timeout = min(_active_timeout_seconds, remaining, _active_total_timeout_seconds)
            try:
                response = await client.post(
                    f"{base_url}/responses",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "User-Agent": _USER_AGENT,
                    },
                    json=payload,
                    timeout=attempt_timeout,
                )
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                # 4xx is the caller's problem — a bad key or a model without
                # x_search access will fail identically on every retry.
                if exc.response.status_code < 500 or attempt >= _active_retries:
                    raise
                last_exc = exc
                log.warning(
                    "x_search.upstream_failure",
                    attempt=attempt + 1,
                    attempts=_active_retries + 1,
                    status=exc.response.status_code,
                )
            except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError) as exc:
                if attempt >= _active_retries:
                    raise
                last_exc = exc
                log.warning(
                    "x_search.transient_failure",
                    attempt=attempt + 1,
                    attempts=_active_retries + 1,
                    error_type=type(exc).__name__,
                )

            backoff = min(_MAX_BACKOFF_SECONDS, 1.5 * (attempt + 1))
            if time.monotonic() + backoff >= deadline:
                break
            await asyncio.sleep(backoff)
            attempt += 1

    if last_exc is not None:
        raise last_exc
    raise httpx.ReadTimeout("x_search exhausted its total timeout budget")


def _degraded_state(
    allowed: list[str],
    excluded: list[str],
    from_date: str,
    to_date: str,
    citations: list[Any],
    inline_citations: list[Any],
) -> tuple[bool, str | None]:
    """Flag an answer that xAI synthesized without matching any indexed post.

    xAI returns 200 with a fluent answer even when its X index has nothing for
    the caller's filters; the answer then comes from training data and looks
    identical to a real one. A broad query with no citations is just an answer,
    so only a *narrowed* query with zero citations counts as degraded.
    """
    active_filters: list[str] = []
    if allowed:
        active_filters.append("allowed_x_handles")
    if excluded:
        active_filters.append("excluded_x_handles")
    if from_date.strip():
        active_filters.append("from_date")
    if to_date.strip():
        active_filters.append("to_date")
    degraded = bool(active_filters) and not citations and not inline_citations
    if not degraded:
        return False, None
    return True, f"no citations returned despite filters: {', '.join(active_filters)}"


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


@tool(
    name="x_search",
    description=(
        "Search X (Twitter) posts, profiles, and threads using xAI's built-in X Search. "
        "Read-only discovery: use it for current discussion, reactions, or claims on "
        "public X rather than general web pages, which belong to web_search. It cannot "
        "post, reply, like, DM, upload media, delete, or read an authenticated X "
        "account. A result with \"degraded\": true was synthesized without matching any "
        "indexed post and must not be reported as something found on X."
    ),
    params={
        "query": {"type": "string", "description": "What to look up on X."},
        "allowed_x_handles": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional X handles to include exclusively (max 10).",
        },
        "excluded_x_handles": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Optional X handles to exclude (max 10). "
                "Cannot be combined with allowed_x_handles."
            ),
        },
        "from_date": {"type": "string", "description": "Optional start date, YYYY-MM-DD."},
        "to_date": {"type": "string", "description": "Optional end date, YYYY-MM-DD."},
        "enable_image_understanding": {
            "type": "boolean",
            "description": "Ask xAI to analyze images attached to matching posts.",
        },
        "enable_video_understanding": {
            "type": "boolean",
            "description": "Ask xAI to analyze videos attached to matching posts.",
        },
    },
    required=["query"],
    execution_timeout_seconds=_ENGINE_TIMEOUT_CEILING_SECONDS,
    result_budget_class="external",
)
@sandboxed(
    kind="web.fetch",
    argv_factory=lambda a: ("x_search", str(a.get("query", ""))),
    record_payload=False,
)
async def x_search(
    query: str,
    allowed_x_handles: list[str] | None = None,
    excluded_x_handles: list[str] | None = None,
    from_date: str = "",
    to_date: str = "",
    enable_image_understanding: bool = False,
    enable_video_understanding: bool = False,
) -> str:
    if not query or not query.strip():
        return _failure("query is required for x_search")

    from agentos.xai_oauth import XaiOAuthError

    try:
        api_key, base_url, credential_source = await _resolve_credential()
    except XaiOAuthError as exc:
        return _failure(str(exc), error_type=exc.code)

    try:
        allowed = _normalize_handles(allowed_x_handles, "allowed_x_handles")
        excluded = _normalize_handles(excluded_x_handles, "excluded_x_handles")
        if allowed and excluded:
            raise ValueError("allowed_x_handles and excluded_x_handles cannot be used together")
        _validate_date_range(from_date, to_date)
        reasoning_effort = _resolve_reasoning_effort()
    except ValueError as exc:
        return _failure(str(exc), error_type="ValueError")

    payload: dict[str, Any] = {
        "model": _active_model,
        "input": [{"role": "user", "content": query.strip()}],
        "tools": [
            _build_tool_definition(
                allowed,
                excluded,
                from_date,
                to_date,
                enable_image_understanding,
                enable_video_understanding,
            )
        ],
        "store": False,
    }
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}

    deadline = time.monotonic() + _active_total_timeout_seconds
    try:
        response = await _post_with_retries(api_key, base_url, payload, deadline)
        data = response.json()
    except httpx.HTTPStatusError as exc:
        log.warning("x_search.failed", status=exc.response.status_code)
        return _failure(_http_error_message(exc), error_type=type(exc).__name__)
    except (httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
        log.warning("x_search.timed_out", total_timeout_seconds=_active_total_timeout_seconds)
        return _failure(
            f"xAI x_search timed out after {_active_total_timeout_seconds:g} seconds",
            error_type=type(exc).__name__,
        )
    except Exception as exc:  # noqa: BLE001 - a tool returns errors, it does not raise them
        log.warning("x_search.failed", error_type=type(exc).__name__)
        return _failure(str(exc), error_type=type(exc).__name__)

    answer = _extract_response_text(data)
    citations = list(data.get("citations") or [])
    inline_citations = _extract_inline_citations(data)
    degraded, degraded_reason = _degraded_state(
        allowed, excluded, from_date, to_date, citations, inline_citations
    )

    return json.dumps(
        {
            "success": True,
            "provider": "xai",
            "credential_source": credential_source,
            "tool": "x_search",
            "model": _active_model,
            "query": query.strip(),
            "answer": answer,
            "citations": citations,
            "inline_citations": inline_citations,
            "degraded": degraded,
            "degraded_reason": degraded_reason,
        },
        ensure_ascii=False,
    )
