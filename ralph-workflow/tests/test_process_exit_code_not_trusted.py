"""Invariant test: pipeline success is determined by empirical evidence, never exit codes."""

from __future__ import annotations

import contextlib
import json
import sys

import pytest

from ralph.pipeline.parallel.coordinator import _has_empirical_evidence
from ralph.process.manager import (
    ProcessManager,
    ProcessManagerPolicy,
    ProcessStatus,
    get_process_manager,
    reset_process_manager,
)

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.3, kill_followup_timeout_s=0.5, log_events=False
)

PYTHON = sys.executable
_EXPECTED_EXIT_CODE = 7


@pytest.fixture(autouse=True)
def _reset_pm():
    reset_process_manager()
    yield
    with contextlib.suppress(Exception):
        get_process_manager().shutdown_all(grace_period_s=0)
    reset_process_manager()


@pytest.mark.asyncio
async def test_exit_code_7_is_exited_not_failed(tmp_path) -> None:
    """ProcessManager records EXITED (not FAILED) even when returncode != 0."""
    pm = ProcessManager(policy=_FAST_POLICY)
    handle = pm.spawn([PYTHON, "-c", f"import sys; sys.exit({_EXPECTED_EXIT_CODE})"])
    handle.wait()

    assert handle.record.status == ProcessStatus.EXITED
    assert handle.record.returncode == _EXPECTED_EXIT_CODE
    # FAILED is reserved for spawn-time failures (e.g., binary not found)
    assert handle.record.status != ProcessStatus.FAILED


@pytest.mark.asyncio
async def test_empirical_evidence_ignores_exit_code(tmp_path) -> None:
    """_has_empirical_evidence returns False on empty dir and True when artifact present."""
    # No artifacts, no git changes → no empirical evidence
    no_evidence = await _has_empirical_evidence(tmp_path)
    assert no_evidence is False

    # Drop an artifact file → evidence is present regardless of exit code
    artifact_dir = tmp_path / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "dummy.json").write_text(json.dumps({"type": "plan"}), encoding="utf-8")

    has_evidence = await _has_empirical_evidence(tmp_path)
    assert has_evidence is True
