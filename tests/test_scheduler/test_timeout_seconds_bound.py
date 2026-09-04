from __future__ import annotations

from pathlib import Path

import pytest

from agentos.scheduler.ops import SchedulerOps
from agentos.scheduler.persistence import JobStore
from agentos.scheduler.types import ScheduleKind, SessionTarget


def _kw(name: str, **over) -> dict:
    kw = dict(
        name=name,
        handler_key="agent_turn",
        payload={"text": "ping"},
        session_target=SessionTarget.ISOLATED,
        schedule_kind=ScheduleKind.EVERY,
        schedule_value="60",
    )
    kw.update(over)
    return kw


@pytest.mark.parametrize("bad", [-1.0, 0.0, 0.999])
@pytest.mark.asyncio
async def test_create_rejects_low_timeout_seconds(bad, tmp_path: Path) -> None:
    store = JobStore(str(tmp_path / "cron.db"))
    await store.open()
    try:
        ops = SchedulerOps(store)
        with pytest.raises(ValueError, match="timeout_seconds must be >="):
            await ops.add(**_kw(f"low-{bad}", timeout_seconds=bad))
    finally:
        await store.close()


@pytest.mark.parametrize("bad", [86401, 86401.0, 1e12])
@pytest.mark.asyncio
async def test_create_rejects_high_timeout_seconds(bad, tmp_path: Path) -> None:
    store = JobStore(str(tmp_path / "cron.db"))
    await store.open()
    try:
        ops = SchedulerOps(store)
        with pytest.raises(ValueError, match="timeout_seconds must be <="):
            await ops.add(**_kw(f"high-{bad}", timeout_seconds=bad))
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_update_rejects_out_of_range_timeout_seconds(tmp_path: Path) -> None:
    store = JobStore(str(tmp_path / "cron.db"))
    await store.open()
    try:
        ops = SchedulerOps(store)
        job = await ops.add(**_kw("ok"))
        with pytest.raises(ValueError, match="timeout_seconds must be"):
            await ops.update(job.id, timeout_seconds=-1)
        with pytest.raises(ValueError, match="timeout_seconds must be"):
            await ops.update(job.id, timeout_seconds=1e12)
    finally:
        await store.close()
