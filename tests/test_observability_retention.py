from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from agentos.observability.retention import (
    LogRetentionSweeper,
    prune_expired_log_files,
)


def _touch_log_file(path: Path, age_seconds: float = 0.0, size_bytes: int = 100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size_bytes)
    mtime = time.time() - age_seconds
    os.utime(path, (mtime, mtime))


def test_prune_expired_log_files_by_ttl(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    # 1. Old decision log (20 days old)
    _touch_log_file(log_dir / "decisions-20260101.jsonl", age_seconds=20 * 86400, size_bytes=500)
    # 2. Old trace log (25 days old)
    _touch_log_file(log_dir / "traces-20260101.jsonl", age_seconds=25 * 86400, size_bytes=500)
    # 3. Fresh decision log (2 days old)
    _touch_log_file(log_dir / "decisions-20260215.jsonl", age_seconds=2 * 86400, size_bytes=500)
    # 4. Very fresh active log (30 seconds old)
    _touch_log_file(log_dir / "agentos.log", age_seconds=30, size_bytes=500)

    result = prune_expired_log_files(
        log_dir=log_dir,
        retention_days=14,
        max_total_bytes=0,
        debounce_seconds=60.0,
    )

    assert result.files_examined == 4
    assert result.files_pruned == 2
    assert result.bytes_freed == 1000
    assert not (log_dir / "decisions-20260101.jsonl").exists()
    assert not (log_dir / "traces-20260101.jsonl").exists()
    assert (log_dir / "decisions-20260215.jsonl").exists()
    assert (log_dir / "agentos.log").exists()


def test_prune_expired_log_files_by_size_budget(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    # Create 3 files of 1000 bytes each, oldest to newest (all within TTL)
    # Total = 3000 bytes.
    _touch_log_file(log_dir / "decisions-20260210.jsonl", age_seconds=5 * 86400, size_bytes=1000)
    _touch_log_file(log_dir / "decisions-20260212.jsonl", age_seconds=3 * 86400, size_bytes=1000)
    _touch_log_file(log_dir / "decisions-20260214.jsonl", age_seconds=1 * 86400, size_bytes=1000)

    # Budget is 2500 bytes -> oldest file (1000 bytes) should be pruned, leaving 2000 bytes <= 2500
    result = prune_expired_log_files(
        log_dir=log_dir,
        retention_days=30,
        max_total_bytes=2500,
        debounce_seconds=60.0,
    )

    assert result.files_pruned == 1
    assert result.bytes_freed == 1000
    assert not (log_dir / "decisions-20260210.jsonl").exists()
    assert (log_dir / "decisions-20260212").with_suffix(".jsonl").exists()
    assert (log_dir / "decisions-20260214").with_suffix(".jsonl").exists()


@pytest.mark.asyncio
async def test_log_retention_sweeper_maybe_sweep(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    _touch_log_file(log_dir / "traces-old.jsonl", age_seconds=20 * 86400, size_bytes=300)

    sweeper = LogRetentionSweeper(
        log_dir=log_dir,
        retention_days=10,
        max_total_bytes=0,
        sweep_interval_s=60.0,
    )

    result = await sweeper.maybe_sweep()
    assert result is not None
    assert result.files_pruned == 1
    assert not (log_dir / "traces-old.jsonl").exists()

    # Second immediate sweep should skip due to interval throttling
    result2 = await sweeper.maybe_sweep()
    assert result2 is None


def test_prune_expired_log_files_preserves_unrelated_files(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    # Unrelated files parked in logs directory
    _touch_log_file(log_dir / "unrelated_backup.tar.gz", age_seconds=50 * 86400, size_bytes=5000)
    _touch_log_file(log_dir / "other_subsystem_data.csv", age_seconds=50 * 86400, size_bytes=5000)
    _touch_log_file(log_dir / "notes.txt", age_seconds=50 * 86400, size_bytes=5000)

    # Expired managed log file
    _touch_log_file(log_dir / "decisions-old.jsonl", age_seconds=50 * 86400, size_bytes=500)

    result = prune_expired_log_files(
        log_dir=log_dir,
        retention_days=14,
        max_total_bytes=1000,
        debounce_seconds=60.0,
    )

    assert result.files_pruned == 1
    assert not (log_dir / "decisions-old.jsonl").exists()
    assert (log_dir / "unrelated_backup.tar.gz").exists()
    assert (log_dir / "other_subsystem_data.csv").exists()
    assert (log_dir / "notes.txt").exists()


@pytest.mark.asyncio
async def test_boot_build_services_retention_lifecycle(tmp_path: Path) -> None:
    from agentos.gateway.boot import build_services
    from agentos.gateway.config import GatewayConfig

    cfg = GatewayConfig.model_validate(
        {
            "observability": {
                "log_retention_days": 7,
                "log_retention_max_total_mb": 100,
                "log_retention_sweep_interval_s": 10.0,
            }
        }
    )

    svc = await build_services(config=cfg)
    assert svc.log_retention_sweeper is not None
    assert svc.log_retention_task is not None
    assert not svc.log_retention_task.done()

    await svc.close()
    assert svc.log_retention_task is None
