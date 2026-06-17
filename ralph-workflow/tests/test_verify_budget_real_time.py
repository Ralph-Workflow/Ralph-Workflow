"""Real-runtime smoke test for the 60s combined test budget.

Unlike tests/test_verify_invariants.py which tests import-time invariants
via patched copies, this test runs ``make test`` as a real subprocess using
actual time.monotonic() to confirm the budget holds on the current machine.

.. note::
    This test is marked ``subprocess_e2e`` so it is excluded from the
    parallel ``make test`` suite (subprocess execution is real I/O).
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.verify_budget_real_time,
    pytest.mark.timeout_seconds(130),
]

_BUDGET_SECONDS = 60.0


def test_make_test_completes_within_budget() -> None:
    """Run ``make test`` as a subprocess and assert it completes within 60s.

    Uses real time.monotonic() (NOT mocked) to measure elapsed wall-clock
    time. The subprocess is given a 120s timeout for overhead, but the
    real assertion is on elapsed ≤ 60.0s.

    ``PYTEST_WORKERS=auto`` is set in the subprocess env because the
    test budget assertion must hold on the FASTEST realistic worker
    count (a CI machine with many cores can parallelize fully). The
    assertion ``elapsed <= 60.0s`` therefore catches regressions
    against the parallel best case, not just the default 2-worker
    sequential case.
    """
    ralph_workflow_dir = Path(__file__).resolve().parent.parent
    start = time.monotonic()

    result = subprocess.run(
        ["make", "test"],
        cwd=str(ralph_workflow_dir),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**os.environ, "PYTEST_WORKERS": "auto"},
    )

    elapsed = time.monotonic() - start

    # Build failure detail for diagnosis
    detail_lines = [
        f"make test exit code: {result.returncode}",
        f"Elapsed: {elapsed:.2f}s (budget: {_BUDGET_SECONDS:.1f}s)",
    ]
    if result.stdout:
        detail_lines.append(f"--- STDOUT ---\n{result.stdout[-2000:]}")
    if result.stderr:
        detail_lines.append(f"--- STDERR ---\n{result.stderr[-2000:]}")
    failure_detail = "\n".join(detail_lines)

    assert result.returncode == 0, failure_detail
    assert elapsed <= _BUDGET_SECONDS, failure_detail
