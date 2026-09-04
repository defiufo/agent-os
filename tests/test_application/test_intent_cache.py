"""Regression tests for IntentApprovalCache capacity bound (#1109).

``record()`` must keep a fixed maximum number of entries, purge expired
approvals before inserting when full, and evict the oldest live entry.
Approval matching and once/always scope behavior stay unchanged.
"""

from __future__ import annotations

import time

import pytest

from agentos.application.intent_cache import IntentApprovalCache


def test_cache_bounds_entries_and_evicts_oldest() -> None:
    cache = IntentApprovalCache(max_entries=2)
    cache.record("rm /a")
    cache.record("rm /b")
    cache.record("rm /c")

    assert len(cache._entries) == 2  # noqa: SLF001
    assert cache.check("rm /a") is False
    assert cache.check("rm /b") is True
    assert cache.check("rm /c") is True


def test_cache_rejects_non_positive_capacity() -> None:
    with pytest.raises(ValueError, match="max_entries must be positive"):
        IntentApprovalCache(max_entries=0)


def test_cache_purges_expired_before_evicting_live() -> None:
    cache = IntentApprovalCache(default_ttl=60, max_entries=2)
    cache.record("rm /a", ttl=0.01)
    cache.record("rm /b")
    time.sleep(0.02)
    cache.record("rm /c")

    assert len(cache._entries) == 2  # noqa: SLF001
    assert cache.check("rm /a") is False
    assert cache.check("rm /b") is True
    assert cache.check("rm /c") is True


def test_refresh_makes_entry_newest_without_growing() -> None:
    cache = IntentApprovalCache(max_entries=2)
    cache.record("rm /a")
    cache.record("rm /b")
    cache.record("rm /a")
    cache.record("rm /c")

    assert len(cache._entries) == 2  # noqa: SLF001
    assert cache.check("rm /b") is False
    assert cache.check("rm /a") is True
    assert cache.check("rm /c") is True


def test_once_and_always_scopes_unchanged() -> None:
    cache = IntentApprovalCache(max_entries=2)
    cache.record_always("rm /a")
    cache.record("rm /b")
    cache.clear_scope("once")
    assert cache.check("rm /a") is True
    assert cache.check("rm /b") is False
