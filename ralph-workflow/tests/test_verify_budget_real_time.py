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
