"""Retention coordinator wiring test (S-3d).

Asserts that 32 concurrent ``sweep_agent_dir`` calls sharing one
``RetentionPassCoordinator`` coalesce into a single retention pass
(``coordinator.passes == 1``) while still removing every aged sentinel.
"""

from __future__ import annotations

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
    """Create ``_SENTINEL_COUNT`` aged completion sentinels under ``.agent``."""
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    aged_mtime = time.time() - (DEFAULT_MAX_AGE_SECONDS + 3600.0)
    sentinels: list[Path] = []
    for index in range(_SENTINEL_COUNT):
        sentinel = agent_dir / f"completion_seen_old_run_{index}.json"
        sentinel.write_text("{}", encoding="utf-8")
        # Backdate the mtime so the sweep treats it as aged.
        import os

        os.utime(sentinel, (aged_mtime, aged_mtime))
        sentinels.append(sentinel)
    return agent_dir, sentinels


def test_concurrent_sweeps_coalesce_into_one_pass(tmp_path: Path) -> None:
    """32 concurrent sweeps with one coordinator run one pass (AC-09)."""
    _agent_dir, sentinels = _seed_aged_sentinels(tmp_path)
    coordinator = RetentionPassCoordinator()
    keep_run_id = "current-run"
    # Register the keep run so it is never swept (sanity: none of the
    # aged sentinels share this id).
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

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_sweep) for _ in range(_SENTINEL_COUNT)]
        results = [future.result() for future in futures]

    # Exactly one retention wave ran the inner sweep body.
    assert coordinator.passes == 1
    # Every concurrent caller observed the shared removed count.
    assert all(count == _SENTINEL_COUNT for count in results)
    # Every aged sentinel was removed.
    assert all(not sentinel.exists() for sentinel in sentinels)
