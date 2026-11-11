from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ralph.workspace.agent_dir_retention import (
    DEFAULT_MAX_AGE_SECONDS,
    RetentionPassCoordinator,
    sweep_agent_dir,
)

_SENTINEL_COUNT = 32


def _seed_aged_sentinels(tmp_path: Path) -> tuple[Path, list[Path]]:
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    aged_mtime = time.time() - (DEFAULT_MAX_AGE_SECONDS + 3600.0)
    sentinels: list[Path] = []
    for index in range(_SENTINEL_COUNT):
        sentinel = agent_dir / f"completion_seen_old_run_{index}.json"
        sentinel.write_text("{}", encoding="utf-8")
        import os

        os.utime(sentinel, (aged_mtime, aged_mtime))
        sentinels.append(sentinel)
    return agent_dir, sentinels


def test_concurrent_sweeps_coalesce_into_one_pass(tmp_path: Path) -> None:
    _agent_dir, sentinels = _seed_aged_sentinels(tmp_path)
    barrier = threading.Barrier(_SENTINEL_COUNT)
    coordinator = RetentionPassCoordinator(on_wave_acquired=barrier.wait)
    keep_run_id = "current-run"
    from ralph.workspace.agent_dir_retention import register_active_run

    register_active_run(tmp_path, keep_run_id)

    barrier_passes: list[int] = []

    def _sweep() -> int:
        removed = sweep_agent_dir(
            tmp_path,
            keep_run_id=keep_run_id,
            coordinator=coordinator,
        )
        barrier_passes.append(removed)
        return removed

    with ThreadPoolExecutor(max_workers=_SENTINEL_COUNT) as executor:
        futures = [executor.submit(_sweep) for _ in range(_SENTINEL_COUNT)]
        results = [future.result() for future in futures]

    assert coordinator.passes == 1
    assert all(count == _SENTINEL_COUNT for count in results)
    assert all(not sentinel.exists() for sentinel in sentinels)
