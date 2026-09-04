"""Transport-timeout retry cap: timeouts allow fewer retries than other transient errors."""

from __future__ import annotations

from agentos.engine.fallback import FallbackPolicy, ProviderErrorKind
from agentos.provider.failures import is_transport_timeout

# ---------------------------------------------------------------------------
# is_transport_timeout unit tests
# ---------------------------------------------------------------------------


class TestIsTransportTimeout:
    def test_timeout_code(self) -> None:
        assert is_transport_timeout(raw_code="timeout") is True

    def test_timeout_code_case_insensitive(self) -> None:
        assert is_transport_timeout(raw_code="Timeout") is True

    def test_timed_out_in_message(self) -> None:
        assert is_transport_timeout(message="Request timed out: ReadTimeout") is True

    def test_request_error_is_not_timeout(self) -> None:
        assert is_transport_timeout(raw_code="request_error", message="Connection reset") is False

    def test_503_is_not_timeout(self) -> None:
        assert is_transport_timeout(raw_code="503", message="upstream 503") is False

    def test_empty_inputs(self) -> None:
        assert is_transport_timeout() is False


# ---------------------------------------------------------------------------
# FallbackPolicy.should_retry_timeout
# ---------------------------------------------------------------------------


class TestShouldRetryTimeout:
    def test_non_timeout_transport_uses_max_retries(self) -> None:
        """Non-timeout transport errors (connection reset) still get full retries."""
        policy = FallbackPolicy(max_retries=3, max_timeout_retries=1)
        kind = ProviderErrorKind.TRANSPORT_TRANSIENT

        assert policy.should_retry_timeout(kind, 0, is_timeout=False) is True
        assert policy.should_retry_timeout(kind, 1, is_timeout=False) is True
        assert policy.should_retry_timeout(kind, 2, is_timeout=False) is True
        assert policy.should_retry_timeout(kind, 3, is_timeout=False) is False

    def test_timeout_capped_at_max_timeout_retries(self) -> None:
        """Timeout errors are capped at max_timeout_retries (default 1)."""
        policy = FallbackPolicy(max_retries=3, max_timeout_retries=1)
        kind = ProviderErrorKind.TRANSPORT_TRANSIENT

        assert policy.should_retry_timeout(kind, 0, is_timeout=True) is True
        assert policy.should_retry_timeout(kind, 1, is_timeout=True) is False

    def test_timeout_with_zero_retries(self) -> None:
        """When max_timeout_retries=0, timeout errors never retry."""
        policy = FallbackPolicy(max_retries=3, max_timeout_retries=0)
        kind = ProviderErrorKind.TRANSPORT_TRANSIENT

        assert policy.should_retry_timeout(kind, 0, is_timeout=True) is False

    def test_auth_failure_never_retries_even_with_timeout(self) -> None:
        """Auth failures should never retry regardless of timeout flag."""
        policy = FallbackPolicy(max_retries=3, max_timeout_retries=1)
        kind = ProviderErrorKind.AUTH_FAILURE

        assert policy.should_retry_timeout(kind, 0, is_timeout=True) is False
        assert policy.should_retry_timeout(kind, 0, is_timeout=False) is False

    def test_default_max_timeout_retries_is_one(self) -> None:
        """Default policy allows 1 timeout retry."""
        policy = FallbackPolicy()
        assert policy.max_timeout_retries == 1

    def test_backwards_compatible_should_retry(self) -> None:
        """The original should_retry method is unchanged."""
        policy = FallbackPolicy(max_retries=3)
        kind = ProviderErrorKind.TRANSPORT_TRANSIENT

        assert policy.should_retry(kind, 0) is True
        assert policy.should_retry(kind, 2) is True
        assert policy.should_retry(kind, 3) is False
