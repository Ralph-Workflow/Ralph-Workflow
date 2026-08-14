"""The run loop's connections to the cycle timebox must actually be made.

Every pure part of this feature is well covered, and all of it is inert if the
composition root stops handing those parts their inputs. Each connection below
could be cut with the whole suite green: the loop's monotonic sample box (which
alone disables time accumulation, enforcement, publication, the agent warning,
the MCP nag and the artifact gate), the mid-loop legacy-resume initializer, and
the timebox policy threaded into the end-of-run report.

These drive the real inner loop and assert on what it hands its collaborators,
which is what cutting a call site changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.config.enums import Verbosity
from ralph.pipeline import run_loop
from ralph.pipeline import runner as runner_module
from ralph.pipeline.state import PipelineState

if TYPE_CHECKING:
    import pytest


def _run_to_terminal(
    monkeypatch: pytest.MonkeyPatch,
    initial: PipelineState,
    on_step: object = None,
) -> None:
    """Run the real loop, ending it on its first step."""

    def _step(**kwargs: object) -> PipelineState:
        if on_step is not None:
            on_step(kwargs)
        state = kwargs["state"]
        return state.copy_with(phase=kwargs["policy_bundle"].pipeline.terminal_phase)

    monkeypatch.setattr(run_loop._runner_module, "run_pipeline_step", _step)
    runner_module.run(MagicMock(), initial_state=initial, verbosity=Verbosity.QUIET)


def test_the_loop_gives_each_step_a_monotonic_sample_box(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a sample box the runner has no clock and the feature is inert.

    The step then samples nothing, so no time accumulates, no deadline is
    enforced or published, no warning reaches the agent or the MCP server, and
    the artifact gate can never fire — with every other test still green.
    """
    boxes: list[object] = []

    _run_to_terminal(
        monkeypatch,
        PipelineState(phase="development"),
        on_step=lambda kwargs: boxes.append(kwargs.get("_cycle_sample_box")),
    )

    assert boxes
    assert all(box is not None for box in boxes)


def test_the_loop_arms_a_legacy_checkpoint_resumed_mid_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A checkpoint predating the feature must not run its cycle untimed.

    Two call sites arm it and only the other one was covered, so this one could
    be deleted and every mid-loop resume would execute with no deadline.
    """
    armed: list[PipelineState] = []
    real_initializer = run_loop.initialize_legacy_cycle_on_resume

    def _spy(state: PipelineState, pipeline: object) -> PipelineState:
        armed.append(real_initializer(state, pipeline))
        return armed[-1]

    monkeypatch.setattr(run_loop, "initialize_legacy_cycle_on_resume", _spy)

    _run_to_terminal(monkeypatch, PipelineState(phase="development"))

    assert armed
    assert armed[-1].cycle_timebox_active is True


def test_the_loop_threads_the_timebox_policy_into_the_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without it the report silently loses its Limit and Finalization fields."""
    reported: list[dict[str, object]] = []
    monkeypatch.setattr(
        run_loop,
        "emit_run_time_report_safely",
        lambda _root, **kwargs: reported.append(kwargs),
    )

    _run_to_terminal(monkeypatch, PipelineState(phase="development"))

    assert len(reported) == 1
    assert reported[0]["cycle_timebox"] is not None
