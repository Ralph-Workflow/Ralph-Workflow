#!/usr/bin/env python3
"""Time the focused S-3/S-4 test files (S-5 phase 2 helper).

Emits one ``file_time <file> <seconds>`` line per file and one
``total_time <seconds>`` line. Each file is run in a fresh pytest
subprocess so the timing reflects an isolated run.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

_FOCUSED_FILES = (
    "tests/unit/test_burst_scheduler.py",
    "tests/unit/test_burst_scheduler_wiring.py",
    "tests/unit/test_explore_pipeline_invariants.py",
    "tests/unit/test_retention_coordinator_wiring.py",
    "tests/unit/test_workspace_health_host_watch_pressure.py",
    "tests/test_cli_workspace_health.py",
)


def _time_file(rel: str) -> float:
    start = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", rel, "-q", "--no-header", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
    )
    elapsed = time.perf_counter() - start
    if proc.returncode != 0:
        print(f"file_fail {rel} rc={proc.returncode}", file=sys.stderr)
        print(proc.stdout, file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(1)
    return elapsed


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    total = 0.0
    for rel in _FOCUSED_FILES:
        elapsed = _time_file(rel)
        total += elapsed
        print(f"file_time {rel} {elapsed:.3f}")
    print(f"total_time {total:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
