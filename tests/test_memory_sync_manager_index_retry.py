"""Regression tests for issue #638.

``_do_file_sync`` replaces ``_mtimes`` with the fresh scan *before* it
indexes anything, so a path whose ``index_file`` raises is already
recorded as seen. Dropping it there means the watcher diff never
rediscovers it and the file stays unindexed until it is edited again or
the process restarts.
"""

from __future__ import annotations

import os

import pytest

from agentos.memory.sync_manager import MemorySyncManager


class FlakyStore:
    """Store whose ``index_file`` fails for the first N calls on a path."""

    def __init__(self, *, failures: dict[str, int] | None = None) -> None:
        self.indexed: list[str] = []
        self.removed: list[str] = []
        self._failures = dict(failures or {})
        self.remove_failures: dict[str, int] = {}

    async def index_file(
        self,
        *,
        path: str,
        content: str,
        source: object,
        mtime: float | None = None,
    ) -> int:
        self.indexed.append(path)
        remaining = self._failures.get(path, 0)
        if remaining > 0:
            self._failures[path] = remaining - 1
            raise RuntimeError(f"transient index failure for {path}")
        return 1

    def fail_next_index(self, path: str, times: int = 1) -> None:
        self._failures[path] = times

    async def remove_file(self, path: str) -> None:
        self.removed.append(path)
        remaining = self.remove_failures.get(path, 0)
        if remaining > 0:
            self.remove_failures[path] = remaining - 1
            raise RuntimeError(f"transient remove failure for {path}")


def _make_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    memory = workspace / "memory"
    memory.mkdir(parents=True)
    (workspace / "MEMORY.md").write_text("root\n", encoding="utf-8")
    return workspace, memory


def _touch_newer(path) -> None:
    """Bump mtime past the recorded scan without relying on clock ticks."""
    stat = path.stat()
    os.utime(path, (stat.st_atime + 10, stat.st_mtime + 10))


def _manager(store, workspace, memory) -> MemorySyncManager:
    return MemorySyncManager(store=store, workspace_dir=workspace, memory_dir=memory)


@pytest.mark.asyncio
async def test_transient_index_failure_is_retried_on_next_watch_sync(tmp_path):
    workspace, memory = _make_workspace(tmp_path)
    store = FlakyStore(failures={"MEMORY.md": 1})
    manager = _manager(store, workspace, memory)

    await manager.sync(reason="manual")

    assert store.indexed == ["MEMORY.md"]
    assert manager._pending_changes == {"MEMORY.md"}
    assert manager._dirty is True

    # The file is untouched on disk, so the mtime diff alone would find
    # nothing — the retry has to come from the pending queue.
    await manager.sync(reason="watch")

    assert store.indexed == ["MEMORY.md", "MEMORY.md"]
    assert manager._pending_changes == set()
    assert manager._dirty is False


@pytest.mark.asyncio
async def test_persistent_index_failure_stays_pending_and_dirty(tmp_path):
    workspace, memory = _make_workspace(tmp_path)
    store = FlakyStore(failures={"MEMORY.md": 5})
    manager = _manager(store, workspace, memory)

    await manager.sync(reason="manual")
    await manager.sync(reason="watch")
    await manager.sync(reason="watch")

    assert store.indexed == ["MEMORY.md"] * 3
    assert manager._pending_changes == {"MEMORY.md"}
    assert manager._dirty is True


@pytest.mark.asyncio
async def test_clean_sync_does_not_requeue_or_reindex(tmp_path):
    workspace, memory = _make_workspace(tmp_path)
    store = FlakyStore()
    manager = _manager(store, workspace, memory)

    await manager.sync(reason="manual")
    await manager.sync(reason="watch")
    await manager.sync(reason="manual")

    assert store.indexed == ["MEMORY.md"]
    assert manager._pending_changes == set()
    assert manager._dirty is False


@pytest.mark.asyncio
async def test_only_the_failing_path_is_requeued(tmp_path):
    workspace, memory = _make_workspace(tmp_path)
    (memory / "note.md").write_text("note\n", encoding="utf-8")
    store = FlakyStore(failures={"memory/note.md": 1})
    manager = _manager(store, workspace, memory)

    await manager.sync(reason="manual")

    assert sorted(store.indexed) == ["MEMORY.md", "memory/note.md"]
    assert manager._pending_changes == {"memory/note.md"}

    await manager.sync(reason="watch")

    assert store.indexed.count("MEMORY.md") == 1
    assert store.indexed.count("memory/note.md") == 2
    assert manager._pending_changes == set()
    assert manager._dirty is False


@pytest.mark.asyncio
async def test_index_failure_during_initial_sync_is_requeued(tmp_path):
    workspace, memory = _make_workspace(tmp_path)
    store = FlakyStore(failures={"MEMORY.md": 1})
    manager = _manager(store, workspace, memory)

    # start() runs _do_file_sync directly with empty _mtimes, so a failure
    # there is exactly the case the watcher diff can never recover from.
    failures = await manager._do_file_sync()
    manager._requeue(failures, reason="initial")

    assert failures.failed_changes == frozenset({"MEMORY.md"})
    assert manager._pending_changes == {"MEMORY.md"}
    assert manager._dirty is True

    await manager.sync(reason="watch")

    assert store.indexed == ["MEMORY.md", "MEMORY.md"]
    assert manager._pending_changes == set()


@pytest.mark.asyncio
async def test_delete_failure_retry_still_works(tmp_path):
    workspace, memory = _make_workspace(tmp_path)
    doomed = memory / "doomed.md"
    doomed.write_text("bye\n", encoding="utf-8")
    store = FlakyStore()
    manager = _manager(store, workspace, memory)

    await manager.sync(reason="manual")
    assert manager._pending_deletes == set()

    doomed.unlink()
    store.remove_failures["memory/doomed.md"] = 1
    await manager.sync(reason="manual")

    assert store.removed == ["memory/doomed.md"]
    assert manager._pending_deletes == {"memory/doomed.md"}
    assert manager._dirty is True

    await manager.sync(reason="watch")

    assert store.removed == ["memory/doomed.md"] * 2
    assert manager._pending_deletes == set()
    assert manager._dirty is False


@pytest.mark.asyncio
async def test_index_and_delete_failures_requeue_together(tmp_path):
    workspace, memory = _make_workspace(tmp_path)
    doomed = memory / "doomed.md"
    doomed.write_text("bye\n", encoding="utf-8")
    store = FlakyStore()
    manager = _manager(store, workspace, memory)

    await manager.sync(reason="manual")

    doomed.unlink()
    store.remove_failures["memory/doomed.md"] = 1
    (workspace / "MEMORY.md").write_text("root updated\n", encoding="utf-8")
    _touch_newer(workspace / "MEMORY.md")
    store.fail_next_index("MEMORY.md")

    await manager.sync(reason="manual")

    assert manager._pending_changes == {"MEMORY.md"}
    assert manager._pending_deletes == {"memory/doomed.md"}
    assert manager._dirty is True

    await manager.sync(reason="watch")

    assert manager._pending_changes == set()
    assert manager._pending_deletes == set()
    assert manager._dirty is False
