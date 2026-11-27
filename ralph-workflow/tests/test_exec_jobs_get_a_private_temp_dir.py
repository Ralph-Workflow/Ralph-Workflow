from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from ralph.mcp.tools import ExecJobRegistry, JobResult

pytestmark = pytest.mark.subprocess_e2e


def _finished(registry: ExecJobRegistry, job_id: str) -> JobResult:
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        result = registry.wait(job_id, wait_ms=2000, capability="ProcessExecBounded")
        if isinstance(result, JobResult):
            return result
    raise AssertionError("job did not finish")


def _stdout(result: JobResult) -> str:
    output = result.output
    return str(output["stdout"]) if isinstance(output, dict) else str(output)


def test_a_background_job_runs_in_a_private_temp_dir_that_is_removed_when_it_ends() -> None:
    registry = ExecJobRegistry(auto_register_runtime_hooks=False)
    job_id = registry.start(
        ["sh", "-c", 'touch "$TMPDIR/left-behind"; printf "TMP=%s\\n" "$TMPDIR"'],
        timeout_ms=15_000,
        capability="ProcessExecBounded",
    )
    result = _finished(registry, job_id)
    line = next(part for part in _stdout(result).splitlines() if part.startswith("TMP="))
    used = Path(line.removeprefix("TMP="))
    assert used.name.startswith("ra")
    assert not used.exists(), "a finished job's temp dir must be removed"


@pytest.mark.skipif(sys.platform != "darwin", reason="BSD grep setting is macOS-only")
def test_a_background_job_gets_the_safe_grep_setting() -> None:
    registry = ExecJobRegistry(auto_register_runtime_hooks=False)
    job_id = registry.start(
        ["sh", "-c", 'printf "GREP=%s\\n" "$GREP_OPTIONS"'],
        timeout_ms=15_000,
        capability="ProcessExecBounded",
    )
    assert "--exclude-dir=target" in _stdout(_finished(registry, job_id))
