"""Regression coverage for S-3's runner-owned visual capture lifecycle."""

from __future__ import annotations

from pathlib import Path

from ralph.visual.capture_lifecycle import CaptureLifecycle
from ralph.visual.capture_request import CaptureRequest
from ralph.visual.capture_set import CaptureSet
from ralph.visual.policy_facts import DEFAULT_THEMES, REQUIRED_STATES, Viewport


def _capture_set(*, target: str, run_id: str) -> CaptureSet:
    request = CaptureRequest.build(
        target=target,
        viewports=(
            Viewport(name="narrow", width=375, height=812),
            Viewport(name="wide", width=1440, height=900),
        ),
        themes=DEFAULT_THEMES,
        states=REQUIRED_STATES,
    )
    return CaptureSet(target=target, cells=request.matrix, run_id=run_id)


def test_visual_lifecycle_regression_runner_retains_before_and_recaptures_after(
    tmp_path: Path,
) -> None:
    """S-3: development capture is before-agent, fresh after-agent, and matrix-identical."""
    from ralph.pipeline.runner import run_visual_capture_lifecycle

    target = "checkout"
    before = _capture_set(target=target, run_id="capture-before")
    after = _capture_set(target=target, run_id="capture-after")
    lifecycle = CaptureLifecycle(tmp_path, run_id="development-run", cycle_id="cycle-1")
    captures = iter((before, after))

    result = run_visual_capture_lifecycle(
        lifecycle=lifecycle,
        target=target,
        matrix_key="a" * 64,
        design_capture_command="bin/capture --target={target}",
        capture=lambda: next(captures),
        invoke_agent=lambda: "agent result",
    )

    agent_result, retained_before, fresh_after = result
    assert agent_result == "agent result"
    assert retained_before is before
    assert fresh_after is after
    assert lifecycle.require_before_set(target=target, matrix_key="a" * 64).cell_ids == before.cell_ids
