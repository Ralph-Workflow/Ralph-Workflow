"""End-to-end pipeline test: drive ``_run_inner_loop`` and assert that
``_push_status_bar_if_changed`` calls ``display.update_status_bar`` on a
real ``ParallelDisplay`` whenever the (phase, cycle) signature changes,
and skips the update when the signature is unchanged.

This is the integration-boundary regression test that pins the contract
between :func:`ralph.pipeline.run_loop._push_status_bar_if_changed` and
:func:`ralph.display.parallel_display.ParallelDisplay.update_status_bar`.
The previous unit test (a monkeypatched double for the display) verified
the helper's signature-dedupe branch in isolation, but a future regression
in the run-loop wiring (e.g. a missing call site, a typo'd display
attribute, or a swallowed exception that masks the push) would not be
caught by a unit test that does not exercise the real ``_run_inner_loop``
loop body.

The fix is to drive the real loop against:
  1. A real ``ParallelDisplay`` composed with a ``_TtyLikeStringIO``
     console so the StatusBar real-TTY gate passes (the same pattern
     already pinned by ``tests/display/test_status_bar.py::
     test_status_bar_live_region_renders_updated_model_on_tty_like_stream``).
  2. A monkeypatched ``_runner_module.run_pipeline_step`` that returns a
     known ``PipelineState`` with a known phase name and known
     ``outer_dev_iteration`` / ``inner_analysis`` counts on successive
     calls, so the test can assert the helper pushes a fresh
     ``StatusBarModel`` exactly when the signature changes and skips the
     push when the signature is unchanged.
  3. A real policy bundle loaded from the default bundled policies so
     ``_build_status_bar_model`` can resolve the human phase label and
     the iteration caps deterministically.

The test uses ``force_terminal=True`` plus a ``_TtyLikeStringIO`` console
because Rich's ``is_terminal`` flag is True on a force_terminal console
AND the ``isatty()`` conjunct is True on a ``_TtyLikeStringIO`` —
exactly the two conjuncts the real-TTY gate requires. The test does NOT
spawn a real subprocess, does NOT use ``time.sleep``, and runs in well
under 1 second wall-clock.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

from rich.console import Console

from ralph.config.verbosity import Verbosity
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.status_bar import StatusBar, StatusBarModel
from ralph.pipeline.run_loop import _LoopContext, _run_inner_loop
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.connectivity import ConnectivityState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    import pytest

    from ralph.policy.models import PolicyBundle


class _TtyLikeStringIO(io.StringIO):
    """An in-memory buffer that reports ``isatty() is True``.

    Mirrors the canonical pattern from
    ``tests/display/test_status_bar.py:58-65`` so the StatusBar real-TTY
    gate (the ``console.is_terminal AND console.file.isatty()`` conjunct)
    passes on a StringIO-backed console without requiring an actual
    pseudo-tty.
    """

    def isatty(self) -> bool:
        return True


def _load_default_policy() -> PolicyBundle:
    """Load the bundled default policy bundle for a deterministic, no-disk test.

    ``load_policy`` reads from a config directory; passing a tempdir
    with no TOML files forces it to fall back to the bundled defaults,
    which is the same shape used by the existing
    ``tests/pipeline/test_run_loop_waiting_state_real_controller.py``
    helper.
    """
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _patched_update_recorder(
    monkeypatch: pytest.MonkeyPatch, _pd: ParallelDisplay
) -> list[StatusBarModel]:
    """Wrap ``StatusBar.update`` to record every push into a shared list.

    The real ``ParallelDisplay.update_status_bar`` calls
    ``self._status_bar.update(model)``, so the production contract is
    that every push flows through ``StatusBar.update``. Both
    ``ParallelDisplay`` (``__slots__``-bound) and ``StatusBar``
    (``__slots__``-bound) reject ``setattr`` on the instance, so the
    only available seam is to patch ``StatusBar.update`` on the class
    itself. The wrapper still sets ``self._model`` so the StatusBar's
    observable state (``last_model``) reflects the push.

    Returns the list so callers can read the captured ``StatusBarModel``
    instances after the run loop completes. A future regression that
    bypasses the StatusBar (e.g. by reading ``last_model`` directly
    from the loop and short-circuiting the push) would still be caught
    because the test inspects the captured push list, not the
    StatusBar's state.
    """
    captured: list[StatusBarModel] = []

    def recording_update(self: StatusBar, model: StatusBarModel) -> None:
        if not isinstance(model, StatusBarModel):
            msg = f"StatusBar.update requires a StatusBarModel, got {type(model).__name__}"
            raise TypeError(msg)
        captured.append(model)
        # Still set the ``_model`` slot so the real lifecycle is
        # exercised end-to-end; this keeps the StatusBar's
        # ``last_model`` property in sync with the captured pushes.
        self._lock.acquire()
        try:
            self._model = model
        finally:
            self._lock.release()

    monkeypatch.setattr(StatusBar, "update", recording_update)
    return captured


def _make_loop_context(
    *,
    active_display: ParallelDisplay,
    workspace_root: Path,
    policy_bundle: PolicyBundle,
) -> _LoopContext:
    """Build a ``_LoopContext`` populated with real display + policy_bundle.

    Mirrors the construction pattern from
    ``tests/pipeline/test_run_loop_waiting_state_real_controller.py:
    _build_run_loop_context`` but with real ``active_display`` and real
    ``policy_bundle`` so ``_build_status_bar_model`` (called from inside
    the run loop) can read the policy's phase definitions and resolve
    the human phase label / iteration caps deterministically.
    """
    # ``display_context`` is read only by side-band helpers; pass a
    # real ``DisplayContext`` built from the same tty-like console so
    # any downstream reader sees the same terminal metrics.
    buf = _TtyLikeStringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        width=120,
        color_system="standard",
    )
    display_context = make_display_context(console=console, env={})

    # Minimal connectivity monitor: always online, so the loop does not
    # block on the offline branch. The Protocol expects an enum-like
    # ``current_state`` attribute, so a plain string would fail the
    # Protocol check; pass the canonical ``ConnectivityState.ONLINE``
    # enum so mypy is happy and the runtime check passes.
    class _OnlineMonitor:
        current_state: ConnectivityState = ConnectivityState.ONLINE

    # The LoopContext dataclass types its slots with Protocol types that
    # only exist under ``TYPE_CHECKING``; we cast to the public object
    # type so the runtime dataclass construction succeeds.
    return _LoopContext(
        policy_bundle=policy_bundle,
        workspace_scope=WorkspaceScope(root=workspace_root),
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


def _make_display() -> ParallelDisplay:
    """Build a real ``ParallelDisplay`` composed with a tty-like console.

    The tty-like console (``force_terminal=True`` plus
    ``_TtyLikeStringIO``) is the canonical pattern that makes the
    StatusBar real-TTY gate pass without requiring an actual pseudo-tty.
    """
    buf = _TtyLikeStringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        width=120,
        color_system="standard",
    )
    ctx = make_display_context(console=console, env={})
    return ParallelDisplay(ctx)


def test_run_inner_loop_pushes_status_bar_on_signature_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-03: ``_run_inner_loop`` pushes a fresh ``StatusBarModel`` to
    ``display.update_status_bar`` every time the (phase, cycle)
    signature changes.

    Drives the real loop body with a monkeypatched
    ``_runner_module.run_pipeline_step`` that returns a fresh
    ``PipelineState`` on each call, with phases that map onto distinct
    signatures. The recording wrapper captures every
    ``update_status_bar`` invocation; the test asserts that the
    captured pushes contain a ``StatusBarModel`` for each distinct
    signature and that the captured phase label / iteration match the
    patched-runner output.
    """
    workspace_root = Path("/tmp/wt028-statusbar-e2e")
    policy_bundle = _load_default_policy()

    pd = _make_display()
    # Sanity: the StatusBar gate passes on a tty-like stream.
    assert pd._ctx.console.is_terminal is True
    assert pd._ctx.console.file.isatty() is True
    assert pd.status_bar is not None

    terminal_phase = policy_bundle.pipeline.terminal_phase

    # Patch the runner's pipeline-step function so each call returns a
    # NEW PipelineState with a distinct phase. The phases are chosen so
    # the (phase, outer_dev_iteration, inner_analysis) signature changes
    # on every call:
    #   - ``development_final_commit`` is a lifecycle phase that
    #     increments the ``iteration`` counter, so its model has
    #     ``outer_dev_iteration=1, outer_dev_cap=5``.
    #   - ``complete`` is the terminal phase, so its model has
    #     ``outer_dev_iteration=None`` (no counter owner).
    # This drives the real ``_run_inner_loop`` loop body without
    # spawning a subprocess and without using real wall-clock waits.
    next_phase: list[str] = ["development_final_commit", "complete"]

    def mock_run_pipeline_step(*_args: object, **_kwargs: object) -> PipelineState:
        phase = next_phase.pop(0) if next_phase else terminal_phase
        chain = AgentChainState(agents=["claude"], current_index=0, retries=0)
        return PipelineState(
            phase=phase,
            phase_chains={phase: chain},
            outer_progress={"iteration": 0},
            budget_caps={"iteration": 5},
        )

    monkeypatch.setattr("ralph.pipeline.runner.run_pipeline_step", mock_run_pipeline_step)
    captured = _patched_update_recorder(monkeypatch, pd)

    chain = AgentChainState(agents=["claude"], current_index=0, retries=0)
    state = PipelineState(
        phase="development_final_commit",
        phase_chains={"development_final_commit": chain},
        outer_progress={"iteration": 0},
        budget_caps={"iteration": 5},
    )

    loop_ctx = _make_loop_context(
        active_display=pd,
        workspace_root=workspace_root,
        policy_bundle=policy_bundle,
    )

    _run_inner_loop(state, loop_ctx, prev_phase="development_final_commit")

    # The helper must have pushed exactly two StatusBarModels:
    # one for the ``development_final_commit`` signature and one for
    # the ``complete`` signature. The recording wrapper guarantees we
    # capture the pushes the loop dispatched.
    assert len(captured) == 2, (
        f"expected exactly 2 update_status_bar pushes (one per signature), "
        f"got {len(captured)}: phase_labels={[m.phase_label for m in captured]!r}"
    )
    first_push, second_push = captured
    assert isinstance(first_push, StatusBarModel)
    assert isinstance(second_push, StatusBarModel)

    # First push corresponds to the
    # (development_final_commit, 1, None) signature — outer_dev_iteration
    # is 1-indexed (completed+1 from PhaseEntryModel) and the cap is the
    # budget_caps['iteration'] value.
    assert first_push.phase_label == "Development Final Commit"
    assert first_push.outer_dev_iteration == 1
    assert first_push.outer_dev_cap == 5
    # Second push corresponds to the terminal phase signature.
    assert second_push.phase_label == "Complete"


def test_run_inner_loop_dedupes_status_bar_on_unchanged_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-03: ``_push_status_bar_if_changed`` skips the
    ``update_status_bar`` call when the (phase, cycle) signature is
    unchanged from the previous loop iteration.

    Drives the real loop body with a monkeypatched runner that returns
    a state with the SAME phase and the SAME iteration counts on every
    call. The loop exits when the runner returns an int (which the run
    loop treats as an early-exit signal before the status bar push).

    The test asserts that ``update_status_bar`` is called exactly
    ONCE: the initial push for the first signature, with no further
    pushes on the unchanged-signature iterations.
    """
    workspace_root = Path("/tmp/wt028-statusbar-e2e-dedup")
    policy_bundle = _load_default_policy()

    pd = _make_display()
    assert pd._ctx.console.is_terminal is True
    assert pd._ctx.console.file.isatty() is True

    # The patched runner returns the SAME phase
    # (``development_final_commit``) and the SAME iteration counts on
    # every call so the signature
    # (state.phase, model.outer_dev_iteration, model.inner_analysis)
    # is identical on every iteration. We exit the loop by having the
    # runner return an int (the run loop's early-exit signal — see
    # run_loop.py:447 ``if isinstance(step_result, int): return ...``),
    # which is read BEFORE the status-bar push so the helper sees no
    # signature change and skips the push.
    iteration_count = {"n": 0}

    def mock_run_pipeline_step(*_args: object, **_kwargs: object) -> PipelineState | int:
        iteration_count["n"] += 1
        if iteration_count["n"] >= 3:
            # The third call returns an int to short-circuit the loop.
            return 0
        chain = AgentChainState(agents=["claude"], current_index=0, retries=0)
        return PipelineState(
            phase="development_final_commit",
            phase_chains={"development_final_commit": chain},
            outer_progress={"iteration": 0},
            budget_caps={"iteration": 5},
        )

    monkeypatch.setattr("ralph.pipeline.runner.run_pipeline_step", mock_run_pipeline_step)
    captured = _patched_update_recorder(monkeypatch, pd)

    chain = AgentChainState(agents=["claude"], current_index=0, retries=0)
    state = PipelineState(
        phase="development_final_commit",
        phase_chains={"development_final_commit": chain},
        outer_progress={"iteration": 0},
        budget_caps={"iteration": 5},
    )

    loop_ctx = _make_loop_context(
        active_display=pd,
        workspace_root=workspace_root,
        policy_bundle=policy_bundle,
    )

    _run_inner_loop(state, loop_ctx, prev_phase="development_final_commit")

    # AC-03 dedupe: the helper pushes exactly ONE StatusBarModel for
    # the (development_final_commit, 1, None) signature; the dedupe at
    # run_loop.py:395 ``if signature != last_sig`` must suppress every
    # subsequent push on the same signature, and the int early-exit
    # path (run_loop.py:447) returns before the third call's push so
    # the second call is also deduped.
    assert len(captured) == 1, (
        f"expected exactly one update_status_bar push (dedupe), got {len(captured)}: "
        f"{[m.phase_label for m in captured]!r}"
    )
    only_push = captured[0]
    assert isinstance(only_push, StatusBarModel)
    assert only_push.phase_label == "Development Final Commit"
    assert only_push.outer_dev_iteration == 1
    assert only_push.outer_dev_cap == 5
