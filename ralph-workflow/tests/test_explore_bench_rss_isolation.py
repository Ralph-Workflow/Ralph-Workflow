"""Regression test: the R6.2 RSS metric is immune to parent-process memory history.

The historical harness measured ``cold_build_peak_rss_bytes`` via
``resource.getrusage(resource.RUSAGE_SELF).ru_maxrss``, which is a
*process-lifetime* high-water mark. When the benchmark ran late in
a pytest session that had already allocated hundreds of MB of
working memory (other explore tests, importers, fixtures), that
high-water mark reflected the parent's accumulated RSS rather than
the benchmark's own peak -- a direct violation of R6.6
("must run reliably ... without flaky results") and of the
committed baseline/target comparability.

The harness now measures RSS inside a fresh subprocess via
``/proc/self/status VmHWM`` so the reported peak depends only on
the build, never on prior activity in the benchmark process.

This regression test simulates the historical pollution: it
allocates a 500 MB transient bytearray in the *test* process
(more than the small-workload RSS target of ~107 MB), runs the
cold-build metric on the small fixture workspace, and asserts the
reported ``cold_build_peak_rss_bytes`` is below the committed
small target. With the old in-process ``getrusage`` read, this
test reproduces the 448 MB vs 107 MB failure. With the
subprocess-based ``VmHWM`` read, it passes deterministically.

Per PA-003 the test must be hermetic: it must not import
``subprocess`` or perform real process I/O itself. The child
process is spawned by ``_measure_cold_build`` (production code),
not by this test. The test fits inside the default
``make verify`` profile and the 60s combined budget; it is
unmarked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.mcp.explore._bench_fixtures import REQUIRED_FIXTURES
from ralph.mcp.explore._bench_r6_metrics import measure_cold_build

_TARGETS_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "performance"
    / "explore-index-targets.json"
)


def _small_cold_build_rss_target() -> float:
    """Return the committed small-workload cold-build RSS target in bytes."""
    raw = json.loads(_TARGETS_PATH.read_text())
    workload_block = raw["metrics"]["small"]
    target = workload_block["cold_build_peak_rss_bytes"]
    assert isinstance(target, (int, float))
    return float(target)


def _seed_small_workspace(parent: Path) -> Path:
    """Mirror the bench gate's ``small`` fixture shape on disk."""
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
    """Reported RSS must not rise with prior allocations in the test process.

    Allocates a 500 MB transient bytearray (well over the small
    target of ~107 MB) and immediately runs the cold-build
    metric. The transient buffer is freed before the assertion so
    any ``ru_maxrss`` leakage would have happened while the
    subprocess was running.
    """
    target_bytes = _small_cold_build_rss_target()
    parent_dir = tmp_path / "metric"
    parent_dir.mkdir(parents=True, exist_ok=True)
    workspace = _seed_small_workspace(parent_dir)

    # Hold a 500 MB allocation throughout the subprocess call so
    # the parent's lifetime high-water mark dwarfs the small
    # target. Any leakage through ``ru_maxrss`` or
    # ``getrusage`` (the historical bug) would surface here.
    pollution = bytearray(500 * 1024 * 1024)
    assert len(pollution) >= int(target_bytes)

    metrics = measure_cold_build(workspace, parent_dir=parent_dir)
    del pollution

    measured_rss = float(metrics["cold_build_peak_rss_bytes"])
    # 25% tolerance matches ``measurement_within_target`` for
    # non-noop metrics; the small target is the ceiling the gate
    # already enforces.
    ceiling = target_bytes * 1.25
    assert measured_rss <= ceiling, (
        f"cold_build_peak_rss_bytes={measured_rss} > ceiling {ceiling} "
        f"(target {target_bytes}); measurement is contaminated by "
        f"parent-process memory history"
    )
