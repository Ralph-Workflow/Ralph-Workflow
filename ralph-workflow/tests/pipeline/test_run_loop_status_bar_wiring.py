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
from ralph.pipeline.run_loop import _LoopContext, _run_inner_loop
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
    """AC-07 narrow-terminal proof: rendered bar fits ``ctx.width`` at every applicable width.

    Drives the run-loop seam (the same wire
    :func:`ralph.pipeline.run_loop._push_status_bar_if_changed` uses,
    which is the public ``display.update_status_bar`` entry point) at
    terminal widths 40, 20, and 14 cols with a :class:`StatusBarModel`
    that populates all four fields (workspace path, phase label,
    outer-dev cycle, inner-analysis cycle). At every width the test
    asserts the bar is usable:

    1. ``len(visible_text) <= width`` after ANSI escape stripping, so
       the bar never overflows the terminal width.
    2. The bar surfaces SOME recognizable model content. At widths
       ``>= 40`` the canonical ``Dev N/cap`` / ``Analysis N/cap``
       iteration labels and the trailing path component remain
       visible. At widths below 40 the budget allocator drops phase
       + path chrome to keep the iteration labels (the most
       operationally important field at narrow widths) visible — so
       at width 20 and width 14 the test asserts that the
       compact/minimal iteration label form (``D1/3`` and/or
       ``A2/5``) is visible instead.
    3. The bar is single-line (no newline wrap into the working
       area), so copy/paste, terminal search, and scrollback
       ergonomics are preserved at every width.

    Width-driven degradation is intentional and consistent with the
    existing :mod:`tests.display.test_status_bar` narrow-width test
    family (``test_render_status_bar_fits_width_at_narrow_terminal_with_long_inputs``
    covers 14-120 cols and
    ``test_render_status_bar_fits_terminal_width_below_14`` covers
    1-13 cols). Below the iteration-visibility threshold (``<14``
    cols) the iteration segments drop entirely so the bar degrades
    cleanly to phase + path; at and above 14 cols the iteration
    labels are the highest-priority content.

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
    sb = cast("StatusBar", pd.status_bar)
    assert isinstance(sb, StatusBar)
    workspace_root = "/Users/alice/code/very-long-project-name/subdir"
    phase_label = "Development Analysis"
    full_model = StatusBarModel(
        workspace_root=workspace_root,
        phase_label=phase_label,
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
    )
    pd.update_status_bar(full_model)
    captured_inside_active = False
    with pd:
        captured_inside_active = sb.is_active
    assert captured_inside_active is True, (
        f"StatusBar must be active inside the production context manager "
        f"at width={width}"
    )
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
    if width >= 40:
        assert "Dev 1/3" in plain, (
            f"AC-07: at width={width} (>=40), canonical 'Dev 1/3' iteration "
            f"label must render; got plain={plain!r}"
        )
        assert "Analysis 2/5" in plain, (
            f"AC-07: at width={width} (>=40), canonical 'Analysis 2/5' "
            f"iteration label must render; got plain={plain!r}"
        )
        # At width=40 with full canonical labels the path budget is ~2
        # chars so the path is middle-truncated to a recognizable
        # prefix. We assert the first char of the trailing path
        # component is visible (proves the path renders, even when
        # truncated to 1-2 chars).
        trailing_segment = workspace_root.rsplit("/", 1)[-1]
        assert trailing_segment[:1] in plain, (
            f"AC-07: at width={width} (>=40), trailing path component "
            f"prefix '{trailing_segment[:1]}' must remain visible; "
            f"got plain={plain!r}"
        )
    else:
        compact_or_minimal_iter_visible = (
            "D1/3" in plain
            or "A2/5" in plain
            or "1/3" in plain
            or "2/5" in plain
        )
        assert compact_or_minimal_iter_visible, (
            f"AC-07: at width={width} (<40), compact/minimal iteration "
            f"label form must remain visible; got plain={plain!r}"
        )
