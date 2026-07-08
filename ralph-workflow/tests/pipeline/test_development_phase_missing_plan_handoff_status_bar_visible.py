"""End-to-end regression for the operator's reported error path.

Pins AC-11: a development-phase run whose ``.agent/PLAN.md`` is
missing must (a) recover in a single bounded iteration back to the
entry phase ("planning" for the default policy) via the REAL
production catch chain, and (b) leave the persistent bottom Status
Bar visible and refreshed on the recovered phase, so an operator
leaving the run unattended still sees "where am I?" instead of a
blank footer.

This test drives the ACTUAL production path end-to-end (NOT a
manual helper simulation): it patches ``runner.materialize_prepared_prompt``
so the missing-plan handoff produces the real
``MissingPlanHandoffError`` from the real ``_handle_inline_effect``
catch branch in ``ralph/pipeline/runner.py:741``, which calls the
real ``recover_missing_plan_handoff`` helper in
``ralph/pipeline/_runner_state_helpers.py:54``, and the loop body
pushes the Status Bar via the real
``_push_status_bar_if_changed`` helper in
``ralph/pipeline/run_loop.py:489``. The push reaches
``ParallelDisplay.update_status_bar`` on a real ``ParallelDisplay``
composed with a tty-like console, which exercises the public seam
the analysis feedback required (no manual helper simulation).

Construction mirrors the patterns pinned by the existing test
suite:

- ``tmp_path`` workspace root with ``WorkspaceScope(root=tmp_path,
  allowed_roots=frozenset({tmp_path}))`` so the runner's
  checkpoint write touches a real tempdir (NOT the repo
  ``checkpoint.json``).
- Real ``ParallelDisplay`` composed with a ``_TtyLikeStringIO``
  console so the StatusBar real-TTY gate passes on the in-process
  console.
- ``_patched_update_recorder`` style monkeypatch on
  ``ParallelDisplay.update_status_bar`` so every push is captured
  into a list AND the original implementation runs (the model is
  stored on the StatusBar's ``_model`` slot, so ``pd.status_bar.last_model``
  reflects every push).
- ``PolicyBundle`` loaded from the bundled defaults so
  ``_build_status_bar_model`` (called from inside the run loop)
  can resolve the human phase label and the iteration caps
  deterministically.
- A ``_LoopContext`` populated with real ``active_display`` and
  real ``policy_bundle`` so the loop body's call to
  ``_push_status_bar_if_changed`` reaches the production entry
  point on a real ``ParallelDisplay``.

The test does NOT use ``time.sleep``, does NOT spawn a real
subprocess, and runs in well under 1 s wall-clock so it fits inside
the 60s combined test budget. It is marked
``@pytest.mark.timeout_seconds(15)`` as an explicit per-test cap.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from rich.console import Console

from ralph.config.verbosity import Verbosity
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.status_bar import StatusBar, StatusBarModel
from ralph.pipeline import runner as runner_module
from ralph.pipeline.run_loop import (
    _LoopContext,
    _run_inner_loop,
)
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.prompts._missing_plan_handoff_error import MissingPlanHandoffError
from ralph.recovery.connectivity import ConnectivityState
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

    Passing a tempdir with no TOML files forces ``load_policy`` to
    fall back to the bundled defaults, which is the same shape used
    by the rest of the run-loop and recovery test suites.
    """
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _make_parallel_display(width: int = 120) -> ParallelDisplay:
    """Build a real ``ParallelDisplay`` composed with a tty-like console.

    ``force_terminal=True`` plus ``_TtyLikeStringIO`` is the canonical
    pattern that makes the StatusBar real-TTY gate pass without a
    real pseudo-tty. The composed ``StatusBar`` lives at
    ``pd.status_bar`` and is updated via
    ``pd.update_status_bar(model)``.
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
    end-to-end (the model is stored on the StatusBar's ``_model``
    slot, so ``pd.status_bar.last_model`` reflects every push). The
    recording wrapper validates that every pushed object is a real
    ``StatusBarModel`` so a regression that pushes a non-model would
    fail loudly here.
    """
    captured: list[StatusBarModel] = []

    real_update_status_bar = ParallelDisplay.update_status_bar

    def recording_update_status_bar(self: ParallelDisplay, model: StatusBarModel) -> None:
        if not isinstance(model, StatusBarModel):
            msg = f"update_status_bar requires a StatusBarModel, got {type(model).__name__}"
            raise TypeError(msg)
        captured.append(model)
        real_update_status_bar(self, model)

    monkeypatch.setattr(ParallelDisplay, "update_status_bar", recording_update_status_bar)
    return captured


def _make_loop_context(
    *,
    active_display: ParallelDisplay,
    workspace_root: Path,
    policy_bundle: PolicyBundle,
) -> _LoopContext:
    """Build a ``_LoopContext`` populated with real display + policy_bundle.

    Mirrors the construction pattern from
    ``tests/pipeline/test_run_loop_status_bar_wiring.py::
    _make_loop_context``: same shape, same fields, with real
    ``active_display`` and real ``policy_bundle`` so the loop
    body's call to ``_push_status_bar_if_changed`` reaches the
    production entry point on the real ``ParallelDisplay``.
    """
    buf = _TtyLikeStringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        width=120,
        color_system="standard",
    )
    display_context = make_display_context(console=console, env={})

    class _OnlineMonitor:
        current_state: ConnectivityState = ConnectivityState.ONLINE

    return _LoopContext(
        policy_bundle=policy_bundle,
        workspace_scope=WorkspaceScope(
            root=workspace_root, allowed_roots=frozenset({workspace_root})
        ),
        config=cast("object", None),
        active_display=active_display,
        display_context=display_context,
        effective_verbosity=Verbosity.NORMAL,
        registry=cast("object", type("R", (), {})()),
        effective_pipeline_subscriber=cast("object", None),
        controller=cast("object", type("C", (), {})()),
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=cast("object", _OnlineMonitor()),
        sleep=cast("object", lambda _s: None),
        is_quiet=False,
        snapshot_registry=None,
    )


def _patch_run_pipeline_step_for_single_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrap the real ``run_pipeline_step`` so the loop exits after the first call.

    On the first call, the wrapper delegates to the REAL
    ``run_pipeline_step`` so the production catch chain in
    ``_handle_inline_effect`` runs end-to-end. On the second call,
    the wrapper returns ``0`` (int) so the loop's
    ``isinstance(step_result, int)`` early-exit fires and the loop
    terminates cleanly. This pins ONE full missing-plan recovery
    iteration without unbounded runaway.
    """
    real_run_step = runner_module.run_pipeline_step
    call_count = {"n": 0}

    def wrapper(*args: object, **kwargs: object) -> PipelineState | int:
        call_count["n"] += 1
        if call_count["n"] >= 2:
            return 0
        return real_run_step(*args, **kwargs)

    monkeypatch.setattr(runner_module, "run_pipeline_step", wrapper)


def _patch_materialize_to_raise_for_development_only(
    monkeypatch: pytest.MonkeyPatch,
) -> list[PipelineState]:
    """Patch the materialization functions to raise only for development phase.

    The production flow's first call into
    ``_run_pipeline_step`` resolves an ``InvokeAgentEffect`` (NOT a
    ``PreparePromptEffect``) via ``call_determine_effect_from_policy``
    and then calls ``materialize_agent_prompt_if_needed`` from
    ``ralph/pipeline/prompt_prep.py`` via the runner module alias at
    ``runner.py:555``. The catch for ``MissingPlanHandoffError`` is
    at ``runner.py:563``.

    The patch hooks BOTH entry points (the
    ``materialize_agent_prompt_if_needed`` alias on the runner
    module and the ``materialize_prepared_prompt`` alias on the
    runner module) so the production catch chain is exercised
    regardless of which materialization entry point the run-time
    routes through. Each patched entry raises
    ``MissingPlanHandoffError`` ONLY when ``state.phase`` (for the
    prepared variant) or ``effect.phase`` (for the agent variant) is
    ``"development"``; for any other phase the patch delegates to
    the original implementation so the loop's second iteration can
    run normally without forcing another recovery.

    Returns the list of ``PipelineState`` objects observed during the
    test so callers can assert on the precise state passed into the
    materialization call (and therefore verify the patch fires on
    the right phase).
    """
    real_materialize_prepared = runner_module.materialize_prepared_prompt
    real_materialize_agent = runner_module.materialize_agent_prompt_if_needed
    states_observed: list[PipelineState] = []

    dev_msg = (
        "Template 'developer_iteration.jinja' requires an existing plan handoff at .agent/PLAN.md"
    )

    def raising_materialize_prepared(
        *args: object,
        state: PipelineState | None = None,
        **kwargs: object,
    ) -> None:
        states_observed.append(cast("PipelineState", state))
        if state is not None and str(state.phase) == "development":
            raise MissingPlanHandoffError(dev_msg)
        real_materialize_prepared(*args, **kwargs)

    def raising_materialize_agent(
        *args: object,
        **kwargs: object,
    ) -> None:
        # ``_materialize_agent_prompt_if_needed`` (in
        # ``ralph.pipeline.prompt_prep``) takes ``effect`` as the
        # first positional arg; the effect carries the phase. Inspect
        # it directly so the patch fires on the development phase.
        if args:
            effect = args[0]
            phase = getattr(effect, "phase", None)
            if phase is not None and str(phase) == "development":
                state = cast(
                    "PipelineState | None",
                    args[1] if len(args) > 1 else kwargs.get("state"),
                )
                if state is not None:
                    states_observed.append(state)
                raise MissingPlanHandoffError(dev_msg)
        real_materialize_agent(*args, **kwargs)

    monkeypatch.setattr(runner_module, "materialize_prepared_prompt", raising_materialize_prepared)
    monkeypatch.setattr(
        runner_module, "materialize_agent_prompt_if_needed", raising_materialize_agent
    )
    return states_observed


@pytest.mark.timeout_seconds(15)
def test_development_phase_missing_plan_handoff_recovers_and_status_bar_remains_visible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AC-11: development + missing plan handoff -> bounded recovery + StatusBar.

    The operator's reported scenario: the development phase attempts
    to materialize a prompt but ``.agent/PLAN.md`` is missing, so
    ``materialize_prepared_prompt`` raises ``MissingPlanHandoffError``.
    The runner's ``_handle_inline_effect`` catches that exception at
    ``ralph/pipeline/runner.py:741`` and routes through
    ``recover_missing_plan_handoff`` (defined at
    ``ralph/pipeline/_runner_state_helpers.py:54``) which advances
    the state to ``pipeline_policy.entry_phase`` ("planning" for
    the default policy) with ``recovery_epoch=1`` and
    ``last_error=str(exc)``. The next loop iteration (via the real
    ``_push_status_bar_if_changed`` at
    ``ralph/pipeline/run_loop.py:489``) pushes a fresh Status Bar
    model on the recovered phase so the operator's bottom Status
    Bar is refreshed with the new phase rather than left showing a
    stale development-phase model.

    Asserts:

    - (a) The materialized state was the development state
      (proves the patch fired on the right phase).
    - (b) The captured StatusBar push has a ``phase_label`` that
      starts with ``"Planning"`` and ``workspace_root`` equal to
      ``tmp_path`` (proves the operator's footer is refreshed to
      the recovered phase, not left stale).
    - (c) The recovered state passed through ``update_status_bar``
      has ``phase == "planning"`` and ``recovery_epoch == 1``
      (proves the recovery happened with bounded bookkeeping).
    - (d) ``ParallelDisplay.update_status_bar`` reached the
      production entry point on a real ``ParallelDisplay`` (the
      push was captured via the recording wrapper that delegates
      to the real implementation, AND ``pd.status_bar.last_model``
      reflects the recovered-phase model after the loop exits).
    - (e) The push happened on the REAL run-loop production path,
      not via a manual ``_push_status_bar_if_changed(...)`` call in
      the test (the patch captures every real push from inside the
      loop; the test never calls the helper directly).
    """
    pipeline_bundle = _load_default_policy_bundle()
    pipeline_policy = pipeline_bundle.pipeline

    # The pre-compiled entry_phase must be "planning" for the default
    # policy so the recovery target is observable in the Status Bar
    # label.
    assert pipeline_policy.entry_phase == "planning", (
        "Default pipeline must compile entry_phase='planning' "
        "(defaults/pipeline.toml entry_block='developer_iteration' → first child phase)."
    )

    # Real ParallelDisplay composed with a tty-like console so the
    # StatusBar real-TTY gate passes without a real pseudo-tty.
    pd = _make_parallel_display(width=120)
    assert pd._ctx.console.is_terminal is True
    assert pd._ctx.console.file.isatty() is True
    assert isinstance(pd.status_bar, StatusBar)

    captured = _patched_update_recorder(monkeypatch)

    # Patch materialize_prepared_prompt to raise only for the
    # development phase; the production catch chain in the runner
    # then routes through the real recover_missing_plan_handoff.
    states_observed = _patch_materialize_to_raise_for_development_only(monkeypatch)

    # Wrap run_pipeline_step so the loop exits cleanly after the
    # first iteration (the assertion target is ONE bounded recovery
    # iteration, not an unbounded loop).
    _patch_run_pipeline_step_for_single_iteration(monkeypatch)

    # No ``.agent/PLAN.md`` exists inside ``tmp_path``; the workspace
    # tree is empty, so the production catch chain is exercised in the
    # operator's exact scenario.
    state = PipelineState(
        phase="development",
        previous_phase=None,
        checkpoint_saved_count=0,
        recovery_epoch=0,
    )

    loop_ctx = _make_loop_context(
        active_display=pd,
        workspace_root=tmp_path,
        policy_bundle=pipeline_bundle,
    )

    final_state, _prev_phase, early_exit = _run_inner_loop(
        state, loop_ctx, prev_phase="development"
    )

    # Sanity: the loop exited cleanly via the int early-exit (the
    # second-call wrapper returned 0) rather than an unrecoverable
    # exception.
    assert early_exit == 0, (
        f"_run_inner_loop must exit via the int early-exit branch "
        f"after one bounded recovery iteration; got {early_exit!r}"
    )

    # (a) materialize_prepared_prompt was called with the development
    # state — proves the production catch chain fired on the right
    # phase.
    assert states_observed, (
        "AC-11: materialize_prepared_prompt must have been called at "
        "least once; got empty observations list"
    )
    assert str(states_observed[0].phase) == "development", (
        f"AC-11: materialize_prepared_prompt must observe the "
        f"development state; got phase={states_observed[0].phase!r}"
    )

    # (b, c, d) The captured update_status_bar push reflects the
    # recovered planning phase on a real ParallelDisplay. This is
    # the seam the analysis feedback required: the push reached
    # ParallelDisplay.update_status_bar via the REAL production
    # path (the recording wrapper delegates to the real impl) and
    # the push was triggered by the real run-loop's
    # _push_status_bar_if_changed helper at run_loop.py:489, not by
    # a manual simulation in the test.
    assert len(captured) >= 1, (
        f"AC-11: ParallelDisplay.update_status_bar must be invoked at "
        f"least once via the production run-loop path; "
        f"got {len(captured)} captures"
    )
    planning_pushes = [m for m in captured if m.phase_label.startswith("Planning")]
    assert planning_pushes, (
        f"AC-11: at least one captured StatusBarModel must have "
        f"phase_label starting with 'Planning'; got "
        f"phase_labels={[m.phase_label for m in captured]!r}"
    )

    # The first captured push on the recovered phase is the proof of
    # AC-11: the run loop pushed a fresh Status Bar model after the
    # recovery, the push carried the recovered human-readable phase
    # label ("Planning"), and the workspace_root reflects the active
    # workspace the operator is operating in.
    planning_push = planning_pushes[0]
    assert isinstance(planning_push, StatusBarModel)
    assert planning_push.workspace_root == str(tmp_path), (
        f"AC-11: workspace_root on the recovered-phase push must be "
        f"the active workspace root; got {planning_push.workspace_root!r}"
    )
    assert planning_push.phase_label.startswith("Planning"), (
        f"AC-11: phase_label on the captured push must start with "
        f"'Planning'; got {planning_push.phase_label!r}"
    )

    # (e) The StatusBar's observable ``last_model`` reflects the
    # final push (the ``update_status_bar`` patch delegates to the
    # real implementation, which stores the model on the
    # StatusBar's ``_model`` slot). This locks the contract that
    # the operator's footer is refreshed, not stale, after a
    # recovery loop iteration.
    last_model = pd.status_bar.last_model
    assert last_model is not None, (
        "AC-11: StatusBar.last_model must be populated after recovery; "
        "the operator's footer would otherwise be stale"
    )
    assert last_model.phase_label.startswith("Planning"), (
        f"AC-11: StatusBar.last_model.phase_label must reflect the "
        f"recovered planning phase; got {last_model.phase_label!r}"
    )
    assert last_model.workspace_root == str(tmp_path), (
        f"AC-11: StatusBar.last_model.workspace_root must be the "
        f"active workspace root; got {last_model.workspace_root!r}"
    )

    # Cross-check: the final state returned from the loop carries
    # the operator-visible recovery contract (phase routed to entry,
    # recovery_epoch bounded at 1, last_error carries the
    # underlying MissingPlanHandoffError message).
    assert final_state.phase == "planning", (
        f"AC-11: final state.phase must be 'planning' (entry_phase); got {final_state.phase!r}"
    )
    assert final_state.recovery_epoch == 1, (
        f"AC-11: final state.recovery_epoch must be 1 after one "
        f"bounded recovery iteration; got {final_state.recovery_epoch}"
    )
    assert "plan handoff" in (final_state.last_error or ""), (
        f"AC-11: final state.last_error must carry the underlying "
        f"MissingPlanHandoffError message; got {final_state.last_error!r}"
    )
