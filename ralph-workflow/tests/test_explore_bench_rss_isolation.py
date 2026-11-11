from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.mcp.explore._bench_fixtures import REQUIRED_FIXTURES
from ralph.mcp.explore._bench_r6_metrics import measure_cold_build

_TARGETS_PATH = (
    Path(__file__).resolve().parents[1] / "docs" / "performance" / "explore-index-targets.json"
)


def _small_cold_build_rss_target() -> float:
    raw = json.loads(_TARGETS_PATH.read_text())
    workload_block = raw["metrics"]["small"]
    target = workload_block["cold_build_peak_rss_bytes"]
    assert isinstance(target, (int, float))
    return float(target)


def _seed_small_workspace(parent: Path) -> Path:
    workspace = parent / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    for fixture in REQUIRED_FIXTURES:
        for rel_path, content in fixture.workspace_files.items():
            target = workspace / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
    return workspace


@pytest.mark.timeout_seconds(60)
def test_cold_build_rss_unaffected_by_parent_memory_history(tmp_path: Path) -> None:
    target_bytes = _small_cold_build_rss_target()
    parent_dir = tmp_path / "metric"
    parent_dir.mkdir(parents=True, exist_ok=True)
    workspace = _seed_small_workspace(parent_dir)

    pollution = bytearray(500 * 1024 * 1024)
    assert len(pollution) >= int(target_bytes)

    metrics = measure_cold_build(workspace, parent_dir=parent_dir)
    del pollution

    measured_rss = float(metrics["cold_build_peak_rss_bytes"])
    ceiling = target_bytes * 1.25
    assert measured_rss <= ceiling, (
        f"cold_build_peak_rss_bytes={measured_rss} > ceiling {ceiling} "
        f"(target {target_bytes}); measurement is contaminated by "
        f"parent-process memory history"
    )
