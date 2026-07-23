"""End-to-end pipeline test: drive ``_run_inner_loop`` and assert that
``_push_status_bar_if_changed`` calls ``ParallelDisplay.update_status_bar``
on a real ``ParallelDisplay`` whenever the
(``state.phase``, ``outer_dev_iteration``, ``inner_analysis``) signature
changes, and skips the update when the signature is unchanged.

This is the integration-boundary regression test that pins the contract
between :func:`ralph.pipeline.run_loop._push_status_bar_if_changed` and
:func:`ralph.display.parallel_display.ParallelDisplay.update_status_bar`.
The test asserts:

1. The exact ``update_status_bar`` method on the live ``ParallelDisplay``
   is invoked (NOT a sub-seam on ``StatusBar.update``), so a regression
   that drops / replaces / shadows the entry point is caught.
2. The captured ``StatusBarModel`` carries the full data contract — the
   ``workspace_root``, ``phase_label``, ``outer_dev_iteration``,
   ``outer_dev_cap``, ``inner_analysis``, and ``inner_analysis_cap``
   values mirror the patched-runner output.
3. At least one captured push has a non-``None`` ``inner_analysis`` value
   so the analysis-iteration contract is exercised (not only
   commit-phase ``outer_dev_iteration``).

The test drives the real loop body against:

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
under 1 second wall-clock so it fits inside the 60s combined test budget.
"""

from __future__ import annotations

import io
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from rich.console import Console

from ralph.config.verbosity import Verbosity
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.status_bar import StatusBar, StatusBarModel
from ralph.pipeline.run_loop import (
    _LoopContext,
    _push_status_bar_if_changed,
    _run_inner_loop,
    _setup_active_display,
    _sync_live_display_context,
)
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.connectivity import ConnectivityState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
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


_ANSI_ESCAPE_RE: re.Pattern[str] = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    """Return ``text`` with all CSI / SGR escape sequences removed.

    Mirrors the escape-stripping regex used by
    ``ralph.display.status_bar`` so the test can compare the rendered
    buffer against a plain-text target without ANSI noise polluting the
    length measurement.
    """
    return _ANSI_ESCAPE_RE.sub("", text)


def _load_default_policy() -> PolicyBundle:
    """Load the bundled default policy bundle for a deterministic, no-disk test.

    ``load_policy`` reads from a config directory; passing a tempdir
    with no TOML files forces it to fall back to the bundled defaults,
    which is the same shape used by the rest of the run-loop test
    suite.
    """
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _patched_update_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> list[StatusBarModel]:
    """Patch ``ParallelDisplay.update_status_bar`` directly to capture
    every push into a shared list.

    :func:`ParallelDisplay.update_status_bar` is the canonical entry
    point that :func:`ralph.pipeline.run_loop._push_status_bar_if_changed`
    calls — patching the method on the class (not on the ``StatusBar``
    sub-object) keeps the seam aligned with the actual production call
    site. The patch delegates to the original implementation so the
    StatusBar sub-lifecycle is still exercised end-to-end (the model is
    stored on the StatusBar's ``_model`` slot, so the StatusBar's
    observable ``last_model`` reflects every push).

    Returns the list so callers can read the captured ``StatusBarModel``
    instances after the run loop completes.
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
    ``tests/pipeline/test_run_loop_waiting_state_logs.py``
    (``_build_loop_context_and_state``) but with real ``active_display``
    and real ``policy_bundle`` so ``_build_status_bar_model`` (called
    from inside the run loop) can read the policy's phase definitions
    and resolve the human phase label / iteration caps deterministically.
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


def test_run_inner_loop_pushes_status_bar_with_full_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-03 contract: ``_run_inner_loop`` pushes a fresh
    ``StatusBarModel`` to ``ParallelDisplay.update_status_bar`` every
    time the (phase, cycle) signature changes — including pushes with a
    non-``None`` ``inner_analysis`` value (the analysis-phase contract).

    Drives the real loop body with a monkeypatched
    ``_runner_module.run_pipeline_step`` that returns a NEW
    ``PipelineState`` with a distinct phase on the first two calls and
    then the terminal phase on the third call (so the loop exits).
    The recording wrapper patches ``ParallelDisplay.update_status_bar``
    so every push is captured; the test asserts that the captured
    pushes contain a ``StatusBarModel`` whose
    ``(workspace_root, phase_label, outer_dev_iteration,
    outer_dev_cap, inner_analysis, inner_analysis_cap)`` exactly
    matches the model built from each patched-runner state.

    Phases selected so the captured pushes span both halves of the
    contract:

    - ``development_final_commit`` is a lifecycle/commit phase that
      increments the ``iteration`` counter, so its model has
      ``outer_dev_iteration=1, outer_dev_cap=5,
      inner_analysis=None, inner_analysis_cap=None``.
    - ``development_analysis`` is an analysis phase (role="analysis")
      with ``iteration_state_field="development_analysis_iteration"``,
      so a state with
      ``loop_iterations={"development_analysis_iteration": 2}``
      produces ``inner_analysis=3, inner_analysis_cap=10``
      (AnalysisLoopCounter(2, 10).display_iteration).
    """
    workspace_root = Path("/tmp/wt028-statusbar-e2e").resolve()
    policy_bundle = _load_default_policy()
    workspace_root_str = str(workspace_root)

    pd = _make_display()
    assert pd._ctx.console.is_terminal is True
    assert pd._ctx.console.file.isatty() is True
    assert isinstance(pd.status_bar, StatusBar)

    states_to_return: list[PipelineState | int] = [
        PipelineState(
            phase="development_final_commit",
            phase_chains={
                "development_final_commit": AgentChainState(
                    agents=["claude"], current_index=0, retries=0
                ),
            },
            outer_progress={"iteration": 0},
            budget_caps={"iteration": 5},
        ),
        PipelineState(
            phase="development_analysis",
            phase_chains={
                "development_analysis": AgentChainState(
                    agents=["claude"], current_index=0, retries=0
                ),
            },
            outer_progress={"iteration": 0},
            budget_caps={"iteration": 5},
            loop_iterations={"development_analysis_iteration": 2},
        ),
        # Third call returns an int so the loop exits via the
        # ``isinstance(step_result, int)`` early-exit branch (which
        # reads BEFORE the status-bar push), giving us EXACTLY two
        # captured pushes for the two distinct signatures.
        0,
    ]

    def mock_run_pipeline_step(*_args: object, **_kwargs: object) -> PipelineState | int:
        if states_to_return:
            return states_to_return.pop(0)
        return 0

    monkeypatch.setattr("ralph.pipeline.runner.run_pipeline_step", mock_run_pipeline_step)
    captured = _patched_update_recorder(monkeypatch)

    initial_chain = AgentChainState(agents=["claude"], current_index=0, retries=0)
    state = PipelineState(
        phase="development_final_commit",
        phase_chains={"development_final_commit": initial_chain},
        outer_progress={"iteration": 0},
        budget_caps={"iteration": 5},
    )
    loop_ctx = _make_loop_context(
        active_display=pd,
        workspace_root=workspace_root,
        policy_bundle=policy_bundle,
    )

    _run_inner_loop(state, loop_ctx, prev_phase="development_final_commit")

    # Two captured pushes for two distinct signatures:
    #   1. (``development_final_commit``, outer_dev=1, inner=None)
    #      -> ``Development Final Commit`` label, outer iteration 1/5,
    #         no analysis iteration.
    #   2. (``development_analysis``, outer_dev=1, inner=3)
    #      -> ``Development Analysis`` label, outer iteration traced
    #         to 1/5 (the find_commit_counter_from_phase logic reaches
    #         ``development_final_commit_cleanup`` whose
    #         ``increments_counter="iteration"``), analysis iteration
    #         3/10.
    assert len(captured) == 2, (
        f"expected exactly 2 update_status_bar pushes (one per signature), "
        f"got {len(captured)}: phase_labels={[m.phase_label for m in captured]!r}"
    )
    first_push, second_push = captured
    assert isinstance(first_push, StatusBarModel)
    assert isinstance(second_push, StatusBarModel)

    # First push — ``development_final_commit`` lifecycle phase with
    # no analysis-iteration loop_iterations seeded on the state.
    assert first_push.workspace_root == workspace_root_str
    assert first_push.phase_label == "Development Final Commit"
    assert first_push.outer_dev_iteration == 1
    assert first_push.outer_dev_cap == 5
    assert first_push.inner_analysis is None
    assert first_push.inner_analysis_cap is None

    # Second push — ``development_analysis`` analysis phase with a
    # populated development_analysis_iteration loop_iterations entry.
    # The analysis-iteration value proves the helper picked up the
    # inner_analysis data path (not just the outer_dev path).
    assert second_push.workspace_root == workspace_root_str
    assert second_push.phase_label == "Development Analysis"
    assert second_push.outer_dev_iteration == 1
    assert second_push.outer_dev_cap == 5
    assert second_push.inner_analysis == 3
    assert second_push.inner_analysis_cap == 10

    # AC-03 invariant: the StatusBar's observable ``last_model``
    # reflects the final push (the ``update_status_bar`` patch
    # delegates to the real implementation, which stores the model on
    # the StatusBar's ``_model`` slot). This locks the contract that
    # a future regression in the run-loop -> update_status_bar ->
    # StatusBar chain is visible on both sides of the seam.
    assert pd.status_bar.last_model is second_push
    assert pd.status_bar.last_model is not None
    last = pd.status_bar.last_model
    assert last.workspace_root == workspace_root_str
    assert last.phase_label == "Development Analysis"
    assert last.inner_analysis == 3
    assert last.inner_analysis_cap == 10


def test_run_inner_loop_dedupes_status_bar_on_unchanged_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-03: ``_push_status_bar_if_changed`` skips the
    ``update_status_bar`` call when the (phase, cycle) signature is
    unchanged from the previous loop iteration.

    Drives the real loop body with a monkeypatched runner that returns
    a state with the SAME phase and the SAME iteration counts on every
    call (the loop exits early when the runner returns an int). The
    test asserts that ``update_status_bar`` is called exactly ONCE: the
    initial push for the first signature, with no further pushes on the
    unchanged-signature iterations.
    """
    workspace_root = Path("/tmp/wt028-statusbar-e2e-dedup").resolve()
    policy_bundle = _load_default_policy()
    workspace_root_str = str(workspace_root)

    pd = _make_display()
    assert pd._ctx.console.is_terminal is True
    assert pd._ctx.console.file.isatty() is True

    iteration_count = {"n": 0}

    def mock_run_pipeline_step(*_args: object, **_kwargs: object) -> PipelineState | int:
        iteration_count["n"] += 1
        if iteration_count["n"] >= 3:
            return 0
        chain = AgentChainState(agents=["claude"], current_index=0, retries=0)
        return PipelineState(
            phase="development_final_commit",
            phase_chains={"development_final_commit": chain},
            outer_progress={"iteration": 0},
            budget_caps={"iteration": 5},
        )

    monkeypatch.setattr("ralph.pipeline.runner.run_pipeline_step", mock_run_pipeline_step)
    captured = _patched_update_recorder(monkeypatch)

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
    # the (``development_final_commit``, outer_dev=1, inner=None)
    # signature; the dedupe at run_loop.py ``if signature != last_sig``
    # must suppress every subsequent push on the same signature, and
    # the int early-exit path returns before the third call's push so
    # the second call is also deduped.
    assert len(captured) == 1, (
        f"expected exactly one update_status_bar push (dedupe), "
        f"got {len(captured)}: {[m.phase_label for m in captured]!r}"
    )
    only_push = captured[0]
    assert isinstance(only_push, StatusBarModel)
    assert only_push.workspace_root == workspace_root_str
    assert only_push.phase_label == "Development Final Commit"
    assert only_push.outer_dev_iteration == 1
    assert only_push.outer_dev_cap == 5
    assert only_push.inner_analysis is None
    assert only_push.inner_analysis_cap is None


@pytest.mark.parametrize("width", [40, 20, 14])
def test_run_inner_loop_status_bar_fits_at_narrow_widths(width: int) -> None:
    """AC-07 proof at the run-loop seam: workspace + phase stay visible at narrow widths.

    Drives the actual run-loop helper
    :func:`ralph.pipeline.run_loop._push_status_bar_if_changed` (NOT a
    direct ``pd.update_status_bar`` call) at terminal widths 40, 20,
    and 14 cols. This is the AC-07 proof at the seam the analysis
    feedback named: the helper that the run loop body calls every
    iteration to push the Status Bar model through to the composed
    ``ParallelDisplay.update_status_bar``. By driving the helper at
    narrow widths, the test proves the same production wire honors
    AC-07 even at widths that would force width-driven degradation of
    the Status Bar's content layout.

    The test feeds a real :class:`PipelineState` for
    ``development_analysis`` (an analysis phase that exercises BOTH
    the outer-dev and inner-analysis iteration paths) through the
    helper, and asserts:

    1. The captured push reached ``ParallelDisplay.update_status_bar``
       (verified via ``pd.status_bar.last_model`` after the helper
       call, since the production entry point stores the model on the
       StatusBar).
    2. ``len(visible_text) <= width`` after ANSI escape stripping, so
       the bar never overflows the terminal width at any narrow width.
    3. The workspace path AND phase label are visible in some
       recognizable form (the AC-07 minimum contract). Phase shows
       a recognizable prefix of the human phase label
       (e.g. ``Dev`` for ``Development Analysis``); path shows a
       recognizable trailing segment (e.g. ``sub`` for
       ``.../subdir``). At width 40 the canonical ``Dev N/cap`` /
       ``Analysis N/cap`` iteration labels also render; at width 20
       the iteration labels render in compact/minimal form (one or
       both segments may be dropped at very narrow widths to keep
       workspace + phase visible); at width 14 the outer iter may
       render but inner is dropped.
    4. The bar is single-line (no newline wrap into the working
       area), so copy/paste, terminal search, and scrollback
       ergonomics are preserved at every width.

    Width-driven degradation is intentional and consistent with the
    existing :mod:`tests.display.test_status_bar` narrow-width test
    family. The AC-07 invariant is that workspace + phase always
    remain readable; iteration labels may degrade or drop entirely at
    very narrow widths to honour that invariant.

    Reuses the ``_TtyLikeStringIO`` fake-console pattern from the
    existing tests in this file so the StatusBar real-TTY gate passes
    without a real pseudo-tty. The test does NOT spawn a subprocess,
    does NOT use ``time.sleep``, and runs in well under 1s per
    parametrized variant so it fits inside the 60s combined test
    budget.
    """
    buf = _TtyLikeStringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        width=width,
        color_system="standard",
    )
    ctx = make_display_context(console=console, env={})
    pd = ParallelDisplay(ctx)
    assert pd._ctx.console.is_terminal is True
    assert pd._ctx.console.file.isatty() is True
    assert pd._ctx.console.width == width, (
        f"AC-07: console width MUST be {width} for this parametrized variant; "
        f"got {pd._ctx.console.width!r}"
    )
    sb = cast("StatusBar", pd.status_bar)
    assert isinstance(sb, StatusBar)
    workspace_root = Path("/Users/alice/code/very-long-project-name/subdir")
    workspace_root_str = str(workspace_root)
    policy_bundle = _load_default_policy()

    # ``development_analysis`` is an analysis phase that exercises BOTH
    # the outer-dev (``iteration`` counter, cap 5) and inner-analysis
    # (``development_analysis_iteration`` counter, cap 10) paths, so the
    # StatusBar model is fully populated and the bar's narrow-width
    # layout is forced to make every field compete for space.
    state = PipelineState(
        phase="development_analysis",
        phase_chains={
            "development_analysis": AgentChainState(agents=["claude"], current_index=0, retries=0),
        },
        outer_progress={"iteration": 0},
        budget_caps={"iteration": 5},
        loop_iterations={"development_analysis_iteration": 2},
    )

    captured_inside_active = False
    with pd:
        captured_inside_active = sb.is_active
        # Drive the run-loop seam directly. ``last_sig=None`` forces
        # an unconditional first push (the dedupe check skips when the
        # signature differs from ``last_sig`` and starts as None).
        new_sig = _push_status_bar_if_changed(
            pd,
            state,
            policy_bundle,
            workspace_root,
            last_sig=None,
        )
        assert new_sig is not None, (
            f"AC-07: _push_status_bar_if_changed must return a fresh "
            f"signature after a first push at width={width}; got None"
        )
        assert isinstance(new_sig, tuple) and len(new_sig) == 5, (
            f"AC-07: _push_status_bar_if_changed must return a "
            f"(phase, outer, inner, integration_alert, outer_label) tuple; got {new_sig!r}"
        )
    assert captured_inside_active is True, (
        f"StatusBar must be active inside the production context manager at width={width}"
    )

    # The captured push reached the production entry point: the
    # StatusBar's ``last_model`` slot is populated by
    # ``update_status_bar`` forwarding into ``StatusBar.update``.
    pushed_model = sb.last_model
    assert pushed_model is not None, (
        f"AC-07: _push_status_bar_if_changed at width={width} must push "
        f"a StatusBarModel through to the production entry point; "
        f"got sb.last_model is None"
    )
    assert isinstance(pushed_model, StatusBarModel)
    assert pushed_model.workspace_root == workspace_root_str
    assert pushed_model.phase_label == "Development Analysis"

    raw_out = buf.getvalue()
    plain = _strip_ansi(raw_out)
    # The Rich Live region emits a trailing CRLF for cursor positioning
    # after the bar; the bar itself is single-line so splitlines() yields
    # at most one content line followed by an empty fragment.
    visible_lines = [line for line in plain.splitlines() if line.strip()]
    assert len(visible_lines) <= 1, (
        f"AC-07: rendered bar must be single-line at width={width}; "
        f"got {len(visible_lines)} non-empty lines, plain={plain!r}"
    )
    visible = max((len(line) for line in visible_lines), default=0)
    assert visible <= width, (
        f"AC-07: rendered bar must fit the terminal width at width={width}; "
        f"longest visible line has length {visible} > {width}, "
        f"plain={plain!r}"
    )
    if visible_lines:
        content_line = visible_lines[0]
        assert "\n" not in content_line, (
            f"AC-07: bar content must be single-line at width={width}; "
            f"got content_line={content_line!r}"
        )
    # AC-07 narrow-terminal contract: workspace path AND phase label
    # remain visible at every applicable width (the minimum contract).
    # The path budget allocator reserves at least _MIN_PATH_BUDGET
    # chars for path (so the trailing segment of the workspace path
    # is recognizable), and at least _MIN_PHASE_BUDGET chars for
    # phase (so a recognizable prefix of the human phase label
    # renders).
    phase_label = "Development Analysis"
    phase_label_prefixes = (
        phase_label[:3],  # "Dev" -- first 3 chars of "Development Analysis"
        phase_label[:4],  # "Deve"
        phase_label[:2],  # "De"
    )
    phase_visible = any(prefix in plain for prefix in phase_label_prefixes)
    assert phase_visible, (
        f"AC-07: at width={width}, phase label must remain visible "
        f"(any of {phase_label_prefixes!r}); got plain={plain!r}"
    )
    trailing_segment = workspace_root_str.rsplit("/", 1)[-1]
    path_prefixes = (
        trailing_segment[:3],  # "sub"
        trailing_segment[:2],  # "su"
        trailing_segment[:1],  # "s"
    )
    path_visible = any(prefix in plain for prefix in path_prefixes)
    assert path_visible, (
        f"AC-07: at width={width}, trailing workspace path segment must "
        f"remain visible (any of {path_prefixes!r}); got plain={plain!r}"
    )


# ---------------------------------------------------------------------------
# DisplayContext refresh-consistency regression test (analysis-feedback how_to_fix)
# ---------------------------------------------------------------------------


def test_setup_active_display_returns_live_context_object() -> None:
    """``_setup_active_display`` returns the SAME context object the refresher mutates.

    The original implementation returned a snapshot
    ``resolved_ctx = display._ctx`` that was only equal (by identity)
    to ``active._ctx`` at construction time. The width refresher
    installed by the function REPLACED ``active._ctx`` with a new
    ``DisplayContext`` object on every tick, leaving the caller's
    separate reference pointing at the stale original. Code reading
    ``_LoopContext.display_context`` and code reading
    ``active._ctx`` could therefore observe different widths after
    a refresh.

    The fix mutates the existing context in place via
    :func:`ralph.pipeline.run_loop._sync_live_display_context` (using
    ``object.__setattr__`` to bypass ``DisplayContext``'s
    ``frozen=True`` constraint), so the identity of the context
    object is preserved across refreshes. ``_setup_active_display``
    now returns ``active._ctx`` itself so the caller holds the SAME
    object that's mutated in place.

    This test proves the contract directly:

    1. After ``_setup_active_display`` returns, the caller's
       ``display_context`` and ``active._ctx`` are the SAME Python
       object (object identity, not just equal width).
    2. After the refresher fires (simulated by invoking the
       ``on_refresh`` callback directly with a refreshed context
       whose width differs from the original), BOTH
       ``display_context.width`` AND ``active._ctx.width`` observe
       the updated width, with NO divergence.
    3. After the refresh, ``display_context is active._ctx`` is
       still True (object identity preserved across the in-place
       mutation).

    The test does NOT spawn a real subprocess, does NOT use
    ``time.sleep``, and runs in well under 1s.
    """
    workspace_root = Path("/tmp/wt028-display-context-refresh")
    with tempfile.TemporaryDirectory() as d:
        policy_bundle = load_policy(Path(d) / ".agent")

    buf = _TtyLikeStringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        width=120,
        color_system="standard",
    )
    initial_ctx = make_display_context(console=console, env={})
    workspace_scope = WorkspaceScope(root=workspace_root)

    active, display_context, stop_fn = _setup_active_display(
        display=None,
        is_quiet=False,
        display_context=initial_ctx,
        workspace_scope=workspace_scope,
        policy_bundle=policy_bundle,
    )

    try:
        # Identity contract: caller holds the SAME object as active._ctx.
        assert display_context is active._ctx, (
            f"_setup_active_display must return the SAME object as "
            f"active._ctx so the in-place refresher mutation is visible "
            f"to the caller; got display_context is active._ctx -> "
            f"{display_context is active._ctx}"
        )
        original_width = display_context.width
        assert active._ctx.width == original_width

        # Simulate a width refresh: produce a refreshed context whose
        # width differs from the original, then invoke the live-sync
        # callback directly. The original implementation's
        # ``display._ctx = ctx`` would replace ``active._ctx`` (and
        # leave ``display_context`` stale); the in-place mutation in
        # ``_sync_live_display_context`` preserves identity and makes
        # both reads observe the new width.
        refreshed_ctx = active._ctx.refreshed()
        # Force a different width on the refreshed context so the
        # assertion catches the staleness bug.
        object.__setattr__(refreshed_ctx, "width", original_width + 17)
        _sync_live_display_context(active, refreshed_ctx)

        assert active._ctx is display_context, (
            f"Object identity MUST be preserved across the in-place "
            f"refresher mutation; got display_context is active._ctx -> "
            f"{display_context is active._ctx} after refresh"
        )
        assert display_context.width == original_width + 17, (
            f"display_context.width MUST observe the refreshed width "
            f"({original_width + 17}); got {display_context.width} "
            f"(the original would have been {original_width})"
        )
        assert active._ctx.width == original_width + 17, (
            f"active._ctx.width MUST observe the refreshed width "
            f"({original_width + 17}); got {active._ctx.width}"
        )
    finally:
        stop_fn()
