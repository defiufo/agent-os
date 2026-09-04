"""SessionStorage writes must not overlap on its shared SQLite connection."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

import pytest

from agentos.session.models import SessionNode, TranscriptEntry
from agentos.session.storage import SessionStorage, StaleEpochError

_T0 = 1_700_000_000_000
_FIRST_KEY = "agent:main:webchat:direct:first"
_SECOND_KEY = "agent:main:webchat:direct:second"


class _PauseFirstTransaction:
    """Connection proxy that holds the first transaction open for assertions."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self.first_begin_started = asyncio.Event()
        self.release_first_begin = asyncio.Event()
        self.begin_calls = 0
        self.session_write_calls = 0

    def execute(self, sql: str, params: Iterable[Any] = ()) -> Any:
        normalized = " ".join(sql.split())
        if normalized == "BEGIN IMMEDIATE":
            self.begin_calls += 1
            if self.begin_calls == 1:
                return self._begin_and_pause(sql, params)
        if normalized.startswith("INSERT INTO sessions"):
            self.session_write_calls += 1
        return self._connection.execute(sql, params)

    async def _begin_and_pause(self, sql: str, params: Iterable[Any]) -> Any:
        cursor = await self._connection.execute(sql, params)
        self.first_begin_started.set()
        await self.release_first_begin.wait()
        return cursor

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


async def _seed_storage() -> tuple[SessionStorage, dict[str, SessionNode]]:
    storage = SessionStorage(":memory:")
    await storage.connect()
    nodes = {
        key: SessionNode(
            session_key=key,
            session_id=f"session-{index}",
            created_at=_T0,
            updated_at=_T0,
        )
        for index, key in enumerate((_FIRST_KEY, _SECOND_KEY), start=1)
    }
    for node in nodes.values():
        await storage.upsert_session(node)
    return storage, nodes


def _stub_session_reads(storage: SessionStorage, nodes: dict[str, SessionNode]) -> None:
    async def get_session(session_key: str) -> SessionNode | None:
        return nodes.get(session_key)

    storage.get_session = get_session  # type: ignore[method-assign]


async def test_concurrent_explicit_transactions_are_serialized() -> None:
    storage, nodes = await _seed_storage()
    _stub_session_reads(storage, nodes)
    proxy = _PauseFirstTransaction(storage.conn)
    storage._conn = proxy  # noqa: SLF001
    tasks: list[asyncio.Task[None]] = []

    try:
        tasks.append(asyncio.create_task(storage.delete_session(_FIRST_KEY)))
        await proxy.first_begin_started.wait()
        tasks.append(asyncio.create_task(storage.delete_session(_SECOND_KEY)))
        await asyncio.sleep(0)

        assert proxy.begin_calls == 1

        proxy.release_first_begin.set()
        await asyncio.gather(*tasks)
        assert proxy.begin_calls == 2
        assert await storage.count_sessions() == 0
    finally:
        proxy.release_first_begin.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await storage.close()


async def test_committing_writer_waits_for_explicit_transaction() -> None:
    storage, nodes = await _seed_storage()
    _stub_session_reads(storage, nodes)
    proxy = _PauseFirstTransaction(storage.conn)
    storage._conn = proxy  # noqa: SLF001
    tasks: list[asyncio.Task[None]] = []
    new_node = SessionNode(
        session_key="agent:main:webchat:direct:new",
        session_id="session-new",
        created_at=_T0,
        updated_at=_T0,
    )

    try:
        tasks.append(asyncio.create_task(storage.delete_session(_FIRST_KEY)))
        await proxy.first_begin_started.wait()
        tasks.append(asyncio.create_task(storage.upsert_session(new_node)))
        await asyncio.sleep(0)

        assert proxy.session_write_calls == 0

        proxy.release_first_begin.set()
        await asyncio.gather(*tasks)
        assert proxy.session_write_calls == 1
        assert await storage.count_sessions() == 2
    finally:
        proxy.release_first_begin.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await storage.close()


async def test_failed_write_rolls_back_before_releasing_serialization_lock() -> None:
    storage, nodes = await _seed_storage()
    node = nodes[_FIRST_KEY]
    entry = TranscriptEntry(
        session_id=node.session_id,
        session_key=node.session_key,
        message_id="stale-message",
        role="user",
        content="must not be written",
        created_at=_T0,
    )

    try:
        with pytest.raises(StaleEpochError):
            await storage.append_transcript_entry(entry, expected_epoch=1)

        assert storage.conn.in_transaction is False
        await storage.delete_session(_FIRST_KEY)
        assert await storage.count_sessions() == 1
    finally:
        await storage.close()
