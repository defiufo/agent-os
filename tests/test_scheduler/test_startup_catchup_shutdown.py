"""Scheduler shutdown owns the full lifecycle of startup catch-up tasks."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from agentos.scheduler import timer as timer_module
from agentos.scheduler.persistence import JobStore
from agentos.scheduler.timer import SchedulerTimer
from agentos.scheduler.types import CronJob, JobStatus, ScheduleKind, SessionTarget


def _overdue_at_job(job_id: str) -> CronJob:
    scheduled_at = datetime.now(UTC) - timedelta(minutes=1)
    return CronJob(
        id=job_id,
        name=job_id,
        cron_expr=scheduled_at.isoformat(),
        schedule_raw=scheduled_at.isoformat(),
        handler_key="agent_run",
        payload={"kind": "agent_turn", "task": "noop", "agent_id": "main"},
        session_target=SessionTarget.ISOLATED,
        schedule_kind=ScheduleKind.AT,
        next_run_at=scheduled_at,
        status=JobStatus.PENDING,
    )


@pytest.mark.asyncio
async def test_stop_cancels_delayed_startup_catchup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def handler(_job: CronJob) -> str:
        started.set()
        return "done"

    monkeypatch.setattr(
        timer_module,
        "spread_jobs",
        lambda job_ids, *, window_seconds: dict.fromkeys(job_ids, 60.0),
    )

    async with JobStore(":memory:") as store:
        await store.save(_overdue_at_job("late"))
        timer = SchedulerTimer(store, handlers={"agent_run": handler})

        await timer.startup_catchup()

        assert len(timer._catchup_tasks) == 1
        await timer.stop()
        await asyncio.sleep(0)

        assert started.is_set() is False
        assert timer._catchup_tasks == set()


@pytest.mark.asyncio
async def test_completed_startup_catchup_is_removed_from_tracking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(_job: CronJob) -> str:
        return "done"

    monkeypatch.setattr(
        timer_module,
        "spread_jobs",
        lambda job_ids, *, window_seconds: dict.fromkeys(job_ids, 0.0),
    )

    async with JobStore(":memory:") as store:
        await store.save(_overdue_at_job("ready"))
        timer = SchedulerTimer(store, handlers={"agent_run": handler})

        await timer.startup_catchup()
        tasks = tuple(timer._catchup_tasks)

        assert len(tasks) == 1
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)

        assert timer._catchup_tasks == set()
