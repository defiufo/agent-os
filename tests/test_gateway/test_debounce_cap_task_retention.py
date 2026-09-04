"""Regression tests for cap-flush task retention (review feedback on #800).

The cap-triggered flush is dispatched with ``asyncio.create_task``. The event
loop keeps only a weak reference to a task, so a delivery with no strong
reference anywhere can be garbage-collected mid-execution and the whole
batch is silently lost -- the exact failure the buffer cap is meant to
prevent. ``cancel_all`` must also drain in-flight flushes so a batch is not
dropped when the gateway stops.
"""

from __future__ import annotations

import asyncio
import gc

from agentos.channels.types import IncomingMessage
from agentos.gateway._debounce import MAX_COALESCED_MESSAGES, _DefaultDebounceCoordinator


def _msg(content: str = "x") -> IncomingMessage:
    return IncomingMessage(
        sender_id="spammer",
        channel_id="telegram",
        content=content,
        attachments=[],
        metadata={},
    )


async def _fill_to_cap(coord: _DefaultDebounceCoordinator, on_fire, key: str = "tg:chat1") -> None:
    for i in range(MAX_COALESCED_MESSAGES):
        await coord.schedule(key, _msg(f"m{i}"), window_s=3600.0, on_fire=on_fire)


async def test_cap_flush_task_is_retained_by_coordinator():
    """The coordinator holds a strong reference while the flush is in flight."""
    coord = _DefaultDebounceCoordinator()
    gate = asyncio.Event()
    fired: list[object] = []

    async def on_fire(combined: object) -> None:
        await gate.wait()
        fired.append(combined)

    await _fill_to_cap(coord, on_fire)

    # In flight and referenced: without retention this set would be empty and
    # the only reference would be the loop's weak one.
    assert len(coord._deliveries) == 1
    assert not fired

    gate.set()
    await asyncio.gather(*coord._deliveries)
    assert len(fired) == 1
    # add_done_callback discards the finished task -- no unbounded growth.
    assert coord._deliveries == set()


async def test_cap_flush_completes_with_no_local_reference_after_gc():
    """A forced collection between dispatch and completion must not lose the batch."""
    coord = _DefaultDebounceCoordinator()
    delivered = asyncio.Event()
    fired: list[object] = []

    async def on_fire(combined: object) -> None:
        fired.append(combined)
        delivered.set()

    await _fill_to_cap(coord, on_fire)

    # No local name binds the delivery task here; force a full collection
    # before the loop ever gets to run it.
    gc.collect()

    await asyncio.wait_for(delivered.wait(), timeout=5.0)
    assert len(fired) == 1
    assert getattr(fired[0], "coalesced_count") == MAX_COALESCED_MESSAGES


async def test_cancel_all_drains_inflight_cap_delivery():
    """Gateway shutdown waits for a cap-triggered flush instead of dropping it."""
    coord = _DefaultDebounceCoordinator()
    fired: list[object] = []

    async def on_fire(combined: object) -> None:
        await asyncio.sleep(0.2)
        fired.append(combined)

    await _fill_to_cap(coord, on_fire)
    assert not fired  # still in flight

    await coord.cancel_all()

    assert len(fired) == 1
    assert getattr(fired[0], "coalesced_count") == MAX_COALESCED_MESSAGES
    assert coord._deliveries == set()
