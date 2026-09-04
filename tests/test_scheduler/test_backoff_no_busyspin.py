"""Regression: ``next_due_at`` must honor ``backoff_until`` like ``iter_due``.

A recurring job that fails repeatedly gets both a fresh ``next_run_at`` (its
next natural slot) and a ``backoff_until`` further in the future. ``iter_due``
excludes the job until ``backoff_until`` passes, but the pre-fix ``next_due_at``
looked only at ``next_run_at``. The timer loop sleeps until ``next_due_at`` and
then ticks ``iter_due``; when the two disagree the loop wakes early, finds
nothing runnable, clamps its sleep to the floor, and busy-spins on SQLite for
the whole backoff window (up to an hour).

``next_due_at`` must therefore report the *runnable* time,
``max(next_run_at, backoff_until)``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentos.scheduler.persistence import JobStore
from agentos.scheduler.types import CronJob, JobStatus, ScheduleKind, SessionTarget


def _cron_job(
    job_id: str,
    next_run_at: datetime,
    *,
    backoff_until: datetime | None = None,
) -> CronJob:
    return CronJob(
        id=job_id,
        name=job_id,
        cron_expr="* * * * *",
        handler_key="agent_run",
        payload={"kind": "agent_turn", "task": "noop", "agent_id": "main"},
        session_target=SessionTarget.ISOLATED,
        schedule_kind=ScheduleKind.CRON,
        next_run_at=next_run_at,
        status=JobStatus.PENDING,
        backoff_until=backoff_until,
    )


@pytest.mark.asyncio
async def test_next_due_at_honors_backoff_window() -> None:
    now = datetime.now(UTC)
    # next_run_at already elapsed; backoff still 5 min out (typical after a few
    # consecutive failures on a per-minute cron).
    stale_next = now - timedelta(seconds=10)
    backoff = now + timedelta(minutes=5)

    async with JobStore(":memory:") as store:
        await store.save(_cron_job("job-backoff", stale_next, backoff_until=backoff))

        due = await store.next_due_at()
        assert due is not None
        # Pre-fix this returned the stale past next_run_at -> busy-spin.
        assert due == backoff, f"expected runnable time to equal backoff_until, got {due}"

        # And the loop's two queries must agree: nothing is runnable yet.
        runnable = [job async for job in store.iter_due(now)]
        assert runnable == [], "job in backoff must not be yielded by iter_due"


@pytest.mark.asyncio
async def test_next_due_at_uses_next_run_when_no_backoff() -> None:
    now = datetime.now(UTC)
    soon = now + timedelta(minutes=1)

    async with JobStore(":memory:") as store:
        await store.save(_cron_job("job-normal", soon))

        due = await store.next_due_at()
        assert due == soon


@pytest.mark.asyncio
async def test_next_due_at_picks_earliest_runnable_across_jobs() -> None:
    now = datetime.now(UTC)
    async with JobStore(":memory:") as store:
        # Job A: due soon, no backoff.
        await store.save(_cron_job("job-a", now + timedelta(minutes=2)))
        # Job B: next_run_at earlier, but stuck in backoff until later.
        await store.save(
            _cron_job(
                "job-b",
                now - timedelta(seconds=5),
                backoff_until=now + timedelta(minutes=10),
            )
        )

        due = await store.next_due_at()
        # A becomes runnable first (2 min) since B is gated by backoff (10 min).
        assert due == now + timedelta(minutes=2)
