"""End-to-end regression for the operator's reported error path.

Pins AC-11: a development-phase run whose ``.agent/PLAN.md`` is missing
must (a) recover in a single bounded iteration back to the entry phase
("planning" for the default policy), and (b) leave the persistent
bottom Status Bar visible and refreshed on the recovered phase, so an
operator leaving the run unattended still sees "where am I?" instead
of a blank footer.

This test combines the construction pattern from
``tests/pipeline/test_runner_missing_plan_handoff_recovery.py`` (the
recovery contract) with the StatusBar wiring pattern from
``tests/pipeline/test_run_loop_status_bar_wiring.py`` (the persistent
status-bar push contract) into ONE focused regression that exercises
the operator's full reported scenario end-to-end. Construction mirrors
both:

- ``tmp_path`` workspace root with ``WorkspaceScope(root=tmp_path,
  allowed_roots=frozenset({tmp_path}))`` so the runner's checkpoint
  write touches a real tempdir (NOT the repo ``checkpoint.json``).
- Real ``ParallelDisplay`` composed with a ``_TtyLikeStringIO`` console
  so the StatusBar real-TTY gate passes on the in-process console.
- ``_patched_update_recorder`` style monkeypatch on
  ``ParallelDisplay.update_status_bar`` so every push is captured into
  a list AND the original implementation runs (the model is stored on
  the StatusBar's ``_model`` slot, so ``pd.status_bar.last_model``
  reflects every push).
- ``PolicyBundle`` loaded from a tempdir with no TOML so
  ``load_policy`` falls back to the bundled defaults (same as
  ``_load_default_policy`` in the wiring test).
- A ``PipelineState(phase="development", previous_phase=None,
  recovery_epoch=0)`` with no ``.agent/PLAN.md`` inside the workspace.

The test does NOT use ``time.sleep``, does NOT spawn a real subprocess,
and runs in well under 1 s wall-clock so it fits inside the 60s combined
test budget. It is marked ``@pytest.mark.timeout_seconds(15)`` as an
explicit per-test cap.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.status_bar import StatusBar, StatusBarModel
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import PreparePromptEffect
from ralph.pipeline.run_loop import _push_status_bar_if_changed
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


class _TtyLikeStringIO(io.StringIO):
    """An in-memory buffer that reports ``isatty() is True``.

    Mirrors the canonical pattern from
    ``tests/display/test_status_bar.py`` and
    ``tests/pipeline/test_run_loop_status_bar_wiring.py`` so the
    StatusBar real-TTY gate (the ``console.is_terminal AND
    console.file.isatty()`` conjunct) passes on a StringIO-backed
    console without requiring an actual pseudo-tty.
    """

    def isatty(self) -> bool:
        return True


def _load_default_policy_bundle() -> PolicyBundle:
    """Load the bundled default policy bundle for a deterministic, no-disk test.

    Passing a tempdir with no TOML files forces ``load_policy`` to fall
    back to the bundled defaults, which is the same shape used by the
    rest of the run-loop and recovery test suites.
    """
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _make_parallel_display(width: int = 120) -> ParallelDisplay:
    """Build a real ``ParallelDisplay`` composed with a tty-like console.

    ``force_terminal=True`` plus ``_TtyLikeStringIO`` is the canonical
    pattern that makes the StatusBar real-TTY gate pass without a
    real pseudo-tty. The composed ``StatusBar`` lives at
    ``pd.status_bar`` and is updated via ``pd.update_status_bar(model)``.
    """
    buf = _TtyLikeStringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        width=width,
        color_system="standard",
    )
    ctx = make_display_context(console=console, env={})
    return ParallelDisplay(ctx)


def _patched_update_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> list[StatusBarModel]:
    """Patch ``ParallelDisplay.update_status_bar`` to capture every push.

    Mirrors the canonical pattern from
    ``tests/pipeline/test_run_loop_status_bar_wiring.py::
    _patched_update_recorder``: the patch delegates to the original
    implementation so the StatusBar sub-lifecycle is still exercised
    end-to-end (the model is stored on the StatusBar's ``_model`` slot,
    so ``pd.status_bar.last_model`` reflects every push). The recording
    wrapper validates that every pushed object is a real
    ``StatusBarModel`` so a regression that pushes a non-model would
    fail loudly here.
    """
    captured: list[StatusBarModel] = []

    real_update_status_bar = ParallelDisplay.update_status_bar

    def recording_update_status_bar(
        self: ParallelDisplay, model: StatusBarModel
    ) -> None:
        if not isinstance(model, StatusBarModel):
            msg = (
                f"update_status_bar requires a StatusBarModel, "
                f"got {type(model).__name__}"
            )
            raise TypeError(msg)
        captured.append(model)
        real_update_status_bar(self, model)

    monkeypatch.setattr(
        ParallelDisplay, "update_status_bar", recording_update_status_bar
    )
    return captured


@pytest.mark.timeout_seconds(15)
def test_development_phase_missing_plan_handoff_recovers_and_status_bar_remains_visible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AC-11: development + missing plan handoff -> bounded recovery + StatusBar.

    The operator's reported scenario: the development phase attempts to
    materialize a prompt but ``.agent/PLAN.md`` is missing, so
    ``materialize_prompt_for_phase`` raises ``MissingPlanHandoffError``.
    The runner's ``_handle_inline_effect`` catches that exception at
    ``ralph/pipeline/runner.py:741`` and routes through
    ``recover_missing_plan_handoff`` (defined at
    ``ralph/pipeline/_runner_state_helpers.py:54``) which advances the
    state to ``pipeline_policy.entry_phase`` ("planning" for the
    default policy) with ``recovery_epoch=1`` and
    ``last_error=str(exc)``. The next loop iteration pushes a fresh
    Status Bar model on the recovered phase via
    ``_push_status_bar_if_changed`` at
    ``ralph/pipeline/run_loop.py:489``.

    Asserts:

    - (a) the return value of ``handle_inline_effect`` is a
      ``PipelineState`` whose ``.phase == "planning"`` (recovery routes
      back to entry_phase, NOT failed_route, because the default
      ``recovery.cycle_cap`` is far above ``recovery_epoch=0``).
    - (b) the returned state's ``.recovery_epoch == 1`` (one bounded
      iteration; not capped, not unbounded).
    - (c) the returned state's ``.last_error`` preserves the underlying
      ``MissingPlanHandoffError`` message ("plan handoff" is the
      literal substring from
      ``ralph/prompts/materialize.py:660``).
    - (d) ``display.update_status_bar`` is invoked at least once with a
      ``StatusBarModel`` whose ``phase_label`` starts with ``"Planning"``
      (the recovered phase), so the operator's bottom Status Bar is
      refreshed with the new phase rather than left showing a stale
      development-phase model.
    """
    pipeline_bundle = _load_default_policy_bundle()
    pipeline_policy = pipeline_bundle.pipeline
    artifacts_policy = pipeline_bundle.artifacts

    # The pre-compiled entry_phase must be "planning" for the default policy
    # so the recovery target is observable in the Status Bar label.
    assert pipeline_policy.entry_phase == "planning", (
        "Default pipeline must compile entry_phase='planning' "
        "(defaults/pipeline.toml entry_block='developer_iteration' → first child phase)."
    )

    workspace_scope = WorkspaceScope(
        root=tmp_path, allowed_roots=frozenset({tmp_path})
    )

    # Real ParallelDisplay composed with a tty-like console so the
    # StatusBar real-TTY gate passes without a real pseudo-tty.
    pd = _make_parallel_display(width=120)
    assert pd._ctx.console.is_terminal is True
    assert pd._ctx.console.file.isatty() is True
    assert isinstance(pd.status_bar, StatusBar)

    captured = _patched_update_recorder(monkeypatch)

    # No ``.agent/PLAN.md`` exists inside ``tmp_path``; the workspace
    # tree is empty, so the materialization step raises
    # ``MissingPlanHandoffError`` exactly as in the operator's scenario.
    effect = PreparePromptEffect(phase="development")
    state = PipelineState(
        phase="development",
        previous_phase=None,
        checkpoint_saved_count=0,
        recovery_epoch=0,
    )

    result = runner_module.handle_inline_effect(
        effect=effect,
        state=state,
        pipeline_policy=pipeline_policy,
        artifacts_policy=artifacts_policy,
        agents_policy=None,
        registry=None,
        config=None,
        display=pd,
        workspace_scope=workspace_scope,
    )

    # (a) the return value is a PipelineState whose phase is "planning"
    assert isinstance(result, PipelineState), (
        f"handle_inline_effect must return a PipelineState on the "
        f"recovery path, got {type(result).__name__}"
    )
    assert result.phase == "planning", (
        f"Recovered phase must be planning (entry_phase), "
        f"got {result.phase!r}"
    )

    # (b) recovery_epoch is incremented by exactly 1 (bounded)
    assert result.recovery_epoch == 1, (
        f"recovery_epoch must be 1 after one recovery iteration, "
        f"got {result.recovery_epoch}"
    )

    # (c) last_error preserves the underlying MissingPlanHandoffError
    # message. The literal substring "plan handoff" appears in the
    # exception message at ralph/prompts/materialize.py:660.
    last_error = result.last_error or ""
    assert "plan handoff" in last_error, (
        f"last_error must preserve the MissingPlanHandoffError message "
        f"(must contain 'plan handoff'); got {last_error!r}"
    )

    # (d) The run loop pushes a fresh Status Bar model on the
    # recovered phase via _push_status_bar_if_changed. Simulate that
    # exact call here (last_sig=None -> unconditional first push) and
    # verify the captured push carries the recovered "Planning" label.
    new_sig = _push_status_bar_if_changed(
        pd,
        result,
        pipeline_bundle,
        workspace_scope.root,
        last_sig=None,
    )
    assert new_sig is not None, (
        "AC-11: _push_status_bar_if_changed must return a fresh "
        "signature after the recovery push; got None"
    )
    assert isinstance(new_sig, tuple) and len(new_sig) == 3, (
        f"AC-11: _push_status_bar_if_changed must return a "
        f"(phase, outer, inner) tuple; got {new_sig!r}"
    )
    assert new_sig[0] == "planning", (
        f"AC-11: pushed signature phase MUST be the recovered "
        f"'planning' phase; got {new_sig[0]!r}"
    )

    # The captured push reached ParallelDisplay.update_status_bar with
    # the full StatusBarModel contract — phase_label reflects the
    # recovered phase so the operator's bottom footer is refreshed
    # rather than left stale.
    assert len(captured) >= 1, (
        f"AC-11: ParallelDisplay.update_status_bar must be invoked at "
        f"least once after recovery; got {len(captured)} captures"
    )
    planning_pushes = [m for m in captured if m.phase_label.startswith("Planning")]
    assert planning_pushes, (
        f"AC-11: at least one captured StatusBarModel must have "
        f"phase_label starting with 'Planning'; got "
        f"phase_labels={[m.phase_label for m in captured]!r}"
    )

    # The LAST captured push is the recovered planning phase (the run
    # loop pushes once per signature change, and the recovery changed
    # the phase from "development" to "planning").
    last_push = captured[-1]
    assert isinstance(last_push, StatusBarModel)
    assert last_push.phase_label.startswith("Planning"), (
        f"AC-11: the final captured push must reflect the recovered "
        f"planning phase; got phase_label={last_push.phase_label!r}"
    )
    assert last_push.workspace_root == str(workspace_scope.root), (
        f"AC-11: workspace_root on the recovered-phase push must be "
        f"the active workspace root; got {last_push.workspace_root!r}"
    )

    # AC-11 invariant: the StatusBar's observable ``last_model``
    # reflects the final push (the ``update_status_bar`` patch
    # delegates to the real implementation, which stores the model on
    # the StatusBar's ``_model`` slot). This locks the contract that
    # the operator's footer is refreshed, not stale, after a recovery
    # loop iteration.
    last_model = pd.status_bar.last_model
    assert last_model is not None, (
        "AC-11: StatusBar.last_model must be populated after recovery; "
        "the operator's footer would otherwise be stale"
    )
    assert last_model.phase_label.startswith("Planning"), (
        f"AC-11: StatusBar.last_model.phase_label must reflect the "
        f"recovered planning phase; got {last_model.phase_label!r}"
    )

