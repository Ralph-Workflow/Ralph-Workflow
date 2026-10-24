"""wt-028-display: end-to-end runtime visibility for the persistent Status Bar.

The persistent bottom Status Bar is composed by
:class:`ralph.display.parallel_display.ParallelDisplay` and reached via
``pd.status_bar``. The existing unit-level tests prove (a) ``StatusBar.start()``
constructs a Live region on a tty-like stream and (b) ``StatusBar.start()`` is
a no-op on force_terminal+StringIO consoles. The existing wiring test
``tests/pipeline/test_run_loop_status_bar_wiring.py`` proves that
``_push_status_bar_if_changed`` calls ``display.update_status_bar``.

Gap A closed here: this file proves the runtime contract through the
**production entry point** (``ParallelDisplay.__enter__`` then
``ParallelDisplay.start`` then ``StatusBar.start`` then the Live region
becomes active). It enters ``with pd as active:``, pushes a
:class:`StatusBarModel`, and asserts both the observable ``is_active`` /
``last_model`` slots on ``pd.status_bar`` AND the captured buffer
contents (which prove the Live region actually rendered the model data).

The tests deliberately use the update-before-start pattern: ``pd.update_status_bar``
is called BEFORE entering the context manager so the model is captured
into Live's initial renderable and the first renderable frame contains
the model data without waiting for a refresh tick. No ``time.sleep`` is
needed (the audit_test_policy forbids sleep in non-``subprocess_e2e``
tests; both old and new tests prove deterministic rendering).

The tests use the same ``_TtyLikeStringIO`` harness as the existing
``tests/display/test_status_bar.py`` so the StatusBar real-TTY gate
(``console.is_terminal AND console.file.isatty()``) passes
deterministically without a real pseudo-tty.

Tests
-----

- ``test_status_bar_is_active_through_parallel_display_context_manager``:
  context-manager activation and teardown of the composed StatusBar.
- ``test_runtime_entry_point_renders_full_model_in_buffer``: full model
  with workspace + phase + outer_dev + inner_analysis surfaces in the
  Live region's captured buffer.
- ``test_runtime_entry_point_omits_iteration_when_not_applicable``: a
  model with no iteration fields renders the phase label and omits any
  placeholder.
- ``test_quiet_mode_suppresses_status_bar_in_runtime_entry_point``: an
  ``is_quiet=True`` ``ParallelDisplay`` does NOT start the Live region
  and writes nothing to the buffer.
- ``test_non_tty_console_suppresses_status_bar_in_runtime_entry_point``:
  a force_terminal console with a plain ``StringIO`` (no
  ``_TtyLikeStringIO`` override) does NOT start the Live region; the
  isatty() conjunct of the gate is honored through the production entry
  point.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.status_bar import StatusBar, StatusBarModel

pytestmark = pytest.mark.integration


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


def _make_parallel_display(
    *,
    is_quiet: bool = False,
    tty_like: bool = True,
    width: int = 120,
) -> tuple[ParallelDisplay, _TtyLikeStringIO | io.StringIO, Console]:
    """Build a ``ParallelDisplay`` with a tty-like or plain StringIO console.

    ``tty_like=True`` uses :class:`_TtyLikeStringIO` so the StatusBar
    real-TTY gate passes. ``tty_like=False`` uses a plain ``io.StringIO``
    so ``console.file.isatty()`` is False; force_terminal+StringIO
    exercises the second conjunct of the real-TTY gate. ``width``
    controls the Rich Console width (default 120 — the canonical
    external-monitor width used by the existing tests; the AC-01..AC-04
    test passes width=100 to cover a common laptop-monitor width).

    The injected ``Console`` is returned so the test asserts on the
    console IT created (the public Rich surface) instead of reaching
    through ``ParallelDisplay`` into its private context holder.
    """
    buf = _TtyLikeStringIO() if tty_like else io.StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        width=width,
        color_system="standard",
    )
    ctx = make_display_context(console=console, env={})
    return ParallelDisplay(ctx, is_quiet=is_quiet), buf, console


def test_status_bar_is_active_through_parallel_display_context_manager() -> None:
    """Entering the production context manager activates the composed StatusBar;
    exiting tears it down.

    This exercises the production entry point end-to-end:
    ``ParallelDisplay.__enter__`` calls ``ParallelDisplay.start`` which calls
    ``self._status_bar.start()``; ``ParallelDisplay.__exit__`` calls
    ``ParallelDisplay.stop`` which calls ``self._status_bar.stop()``. The
    observable slot ``pd.status_bar.is_active`` flips ``True`` while
    inside the context manager and back to ``False`` after exit. The
    composed :class:`StatusBar` instance is reachable only via
    ``pd.status_bar`` (single-owner invariant).
    """
    pd, _buf, _console = _make_parallel_display()
    sb = pd.status_bar
    assert isinstance(sb, StatusBar)
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
    )
    pre_active_state = sb.is_active
    inside_active_state = pre_active_state
    inside_last_model: StatusBarModel | None = None
    with pd as active:
        assert active is pd
        inside_active_state = sb.is_active
        active.update_status_bar(model)
        inside_last_model = sb.last_model
    after_active_state = sb.is_active
    assert pre_active_state is False
    assert inside_active_state is True
    assert after_active_state is False
    assert inside_last_model is model


def test_runtime_entry_point_renders_full_model_in_buffer() -> None:
    """Full model pushed through the production entry point surfaces in the
    captured buffer.

    ``workspace_root``, ``phase_label``, applicable outer-dev iteration,
    and applicable inner-analysis iteration all appear in the Live
    region's rendered buffer. Uses the update-before-start pattern (model
    captured into Live's initial renderable) so the first renderable
    frame contains the model data deterministically.
    """
    pd, buf, console = _make_parallel_display()
    assert console.is_terminal is True
    assert console.file.isatty() is True
    model = StatusBarModel(
        workspace_root="/tmp/proj",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
    )
    pd.update_status_bar(model)
    captured_inside_active = False
    with pd:
        captured_inside_active = pd.status_bar.is_active
    out = buf.getvalue()
    assert captured_inside_active is True
    assert "/tmp/proj" in out, (
        f"Live region must surface the workspace path '/tmp/proj'; got {out!r}"
    )
    assert "Development" in out, (
        f"Live region must surface the phase label 'Development'; got {out!r}"
    )
    assert "Cycle 1/3" in out, (
        f"Live region must surface the outer-dev iteration 'Cycle 1/3'; got {out!r}"
    )
    assert "iter 2/5" in out, (
        f"Live region must surface the inner-analysis iteration 'iter 2/5'; got {out!r}"
    )


def test_runtime_entry_point_omits_iteration_when_not_applicable() -> None:
    """Phase-conditional omission: when outer_dev and inner_analysis are both
    None, the buffer contains the phase label but no iteration placeholder.

    The persistent Status Bar contract is that a field is omitted
    entirely (no ``--`` placeholder) when its iteration field is ``None``
    on the model. The buffer proves the field is omitted (not rendered
    as ``--`` or similar placeholder).
    """
    pd, buf, console = _make_parallel_display()
    assert console.is_terminal is True
    assert console.file.isatty() is True
    model = StatusBarModel(
        workspace_root="/tmp/proj",
        phase_label="Commit",
        phase_style="theme.phase.development",
        outer_dev_iteration=None,
        outer_dev_cap=None,
        inner_analysis=None,
        inner_analysis_cap=None,
    )
    pd.update_status_bar(model)
    captured_inside_active = False
    with pd:
        captured_inside_active = pd.status_bar.is_active
    out = buf.getvalue()
    assert captured_inside_active is True
    assert "Commit" in out, f"Live region must surface the phase label 'Commit'; got {out!r}"
    assert "Dev " not in out, (
        f"Live region must omit the outer-dev iteration label when the "
        f"iteration is None; got {out!r}"
    )
    assert "iter " not in out, (
        f"Live region must omit the inner-analysis iteration label when "
        f"the iteration is None; got {out!r}"
    )
    assert "--" not in out, (
        f"Live region must not render a '--' placeholder for omitted iteration fields; got {out!r}"
    )


def test_status_bar_shows_workspace_phase_and_applicable_iterations_end_to_end() -> None:
    """AC-01..AC-04 end-to-end: workspace, phase, and applicable iteration counts.

    This focused test names the four prompt acceptance criteria
    (AC-01..AC-04) directly:

    - **AC-01**: the bottom Status Bar visibly shows the working directory,
      the active phase, and any applicable iteration count. The test
      drives the production entry point (``pd.update_status_bar`` followed
      by ``with pd:``) at terminal width 100 and asserts the captured
      live buffer contains the workspace path ``/tmp/ac01-workspace``,
      the phase label ``development``, and the literal iteration labels
      ``Cycle 1/3`` (outer cycle 1 of 3) and ``iter 2/5`` (inner
      analysis cycle 2 of 5).

    - **AC-02**: when a phase does not track an outer-dev or inner
      analysis iteration, the field is omitted entirely (no ``--``
      placeholder). A second model with both iteration fields ``None``
      is pushed through the same production entry point and the
      captured buffer is asserted to contain neither ``Cycle --`` nor
      ``iter --`` nor any ``--/--`` substring.

    - **AC-03**: during development-related phases the outer-dev
      iteration (``Cycle N/cap``) is visible.

    - **AC-04**: during analysis phases the inner analysis iteration
      (``iter N/cap``) is visible.

    The test uses width 100 (a common external-monitor width) because
    the canonical ``Cycle 1/3`` / ``iter 2/5`` labels always render
    in full at widths ``>= _CANONICAL_FIT_THRESHOLD`` (40 cols).
    Reuses the existing ``_TtyLikeStringIO`` fake-console pattern so
    the StatusBar real-TTY gate passes without a real pseudo-tty.
    """
    pd_full, buf_full, console_full = _make_parallel_display(width=120)
    assert console_full.is_terminal is True
    assert console_full.file.isatty() is True
    assert console_full.width == 120, (
        f"AC-01: console width MUST be 120 for this focused test; got {console_full.width!r}"
    )
    sb = pd_full.status_bar
    full_model = StatusBarModel(
        workspace_root="/tmp/ac01-workspace",
        phase_label="development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
    )
    pd_full.update_status_bar(full_model)
    captured_inside_full_active = False
    with pd_full:
        captured_inside_full_active = sb.is_active
    full_out = buf_full.getvalue()
    assert captured_inside_full_active is True, (
        "StatusBar must be active inside the production context manager"
    )
    assert "/tmp/ac01-workspace" in full_out, (
        f"AC-01: Live region must surface the workspace path "
        f"'/tmp/ac01-workspace'; got {full_out!r}"
    )
    assert "development" in full_out, (
        f"AC-01: Live region must surface the phase label 'development'; got {full_out!r}"
    )
    assert "Cycle 1/3" in full_out, (
        f"AC-03: Live region must surface the outer-dev iteration 'Cycle 1/3'; got {full_out!r}"
    )
    assert "iter 2/5" in full_out, (
        f"AC-04: Live region must surface the inner-analysis iteration 'iter 2/5'; got {full_out!r}"
    )

    pd_none, buf_none, _console_none = _make_parallel_display(width=100)
    none_model = StatusBarModel(
        workspace_root="/tmp/ac01-workspace",
        phase_label="commit",
        phase_style="theme.phase.commit",
        outer_dev_iteration=None,
        outer_dev_cap=None,
        inner_analysis=None,
        inner_analysis_cap=None,
    )
    pd_none.update_status_bar(none_model)
    captured_inside_none_active = False
    with pd_none:
        captured_inside_none_active = pd_none.status_bar.is_active
    none_out = buf_none.getvalue()
    assert captured_inside_none_active is True, (
        "StatusBar must be active inside the production context manager "
        "for the no-iteration case too"
    )
    assert "Dev --" not in none_out, (
        f"AC-02: Live region must NOT render a 'Dev --' placeholder when "
        f"the outer-dev iteration is None; got {none_out!r}"
    )
    assert "Analysis --" not in none_out, (
        f"AC-02: Live region must NOT render an 'Analysis --' placeholder "
        f"when the inner-analysis iteration is None; got {none_out!r}"
    )
    assert "--/--" not in none_out, (
        f"AC-02: Live region must NOT render any '--/--' iteration placeholder; got {none_out!r}"
    )
    assert "--" not in none_out, (
        f"AC-02: Live region must NOT render any '--' placeholder for "
        f"omitted iteration fields; got {none_out!r}"
    )
    assert "commit" in none_out, (
        f"AC-01: Live region must still surface the phase label "
        f"'commit' when iterations are None; got {none_out!r}"
    )


def test_quiet_mode_suppresses_status_bar_in_runtime_entry_point() -> None:
    """A quiet-mode ``ParallelDisplay`` does NOT start the Live region through
    the production entry point.

    Even when ``pd.update_status_bar`` is called inside the production
    context manager, an ``is_quiet=True`` ``ParallelDisplay`` keeps the
    StatusBar gated off. The captured buffer is empty so non-interactive
    and quiet runs stay clean.
    """
    pd, buf, console = _make_parallel_display(is_quiet=True)
    assert console.is_terminal is True
    assert console.file.isatty() is True
    sb = pd.status_bar
    model = StatusBarModel(
        workspace_root="/tmp/proj",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
    )
    captured_quiet_active = True
    with pd:
        pd.update_status_bar(model)
        captured_quiet_active = sb.is_active
    out = buf.getvalue()
    assert captured_quiet_active is False
    assert out == "", f"No bytes may be written in quiet mode; got {out!r}"


def test_non_tty_console_suppresses_status_bar_in_runtime_entry_point() -> None:
    """A force_terminal console with a plain ``StringIO`` does NOT start the
    Live region through the production entry point.

    Rich's ``is_terminal`` is True on a force_terminal console; the
    StatusBar real-TTY gate's ``isatty()`` conjunct must keep the bar
    gated off on a plain ``StringIO`` (which has ``isatty() is False``).
    This proves the second conjunct of the gate is honored through the
    production entry point so non-tty files stay clean even when
    force_terminal is set (the same pattern real production code uses
    on CI / redirected output / pipe output).
    """
    pd, buf, console = _make_parallel_display(tty_like=False)
    assert console.is_terminal is True, "force_terminal implies Rich's is_terminal=True"
    assert console.file.isatty() is False, (
        "Plain StringIO is not a TTY (the isatty() conjunct gate check)."
    )
    sb = pd.status_bar
    model = StatusBarModel(
        workspace_root="/tmp/proj",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
    )
    captured_nontty_active = True
    with pd:
        pd.update_status_bar(model)
        captured_nontty_active = sb.is_active
    out = buf.getvalue()
    assert captured_nontty_active is False
    assert out == "", f"No bytes may be written on a force_terminal+StringIO console; got {out!r}"


def test_runtime_entry_point_regression_stop_preserves_prior_transcript_and_banner() -> None:
    """S-1: stopping the live footer keeps earlier CLI output readable."""
    pd, buf, console = _make_parallel_display()
    banner = "ralph 1.2.3 — welcome"
    console.print(banner)
    before_stop = buf.getvalue()
    model = StatusBarModel(
        workspace_root="/tmp/commit-workspace",
        phase_label="Commit Generation",
        phase_style="theme.phase.commit",
    )
    pd.update_status_bar(model)

    with pd:
        assert pd.status_bar.is_active is True
        assert pd.status_bar.last_model is model
        before_stop_footer = buf.getvalue()

    after_stop = buf.getvalue()
    assert "ralph" in before_stop
    assert "welcome" in before_stop
    assert "ralph" in after_stop
    assert "welcome" in after_stop
    assert before_stop in after_stop
    teardown_output = after_stop[len(before_stop_footer) :]
    assert "\x1b[2J" not in teardown_output


def test_runtime_entry_point_renders_evidence_diagnosis_from_retained_recovery() -> None:
    """Retained crash-recovery records produce EVIDENCE_DIAGNOSIS, not CONFLICT_RESOLUTION."""
    from ralph.pipeline.rebase_state import RebaseState

    rebase = RebaseState(recovery_record_retained=True)
    assert rebase.operation_display_state == "evidence_diagnosis"

    pd, buf, _console = _make_parallel_display(width=120)
    model = StatusBarModel(
        workspace_root="/tmp/recovery-workspace",
        phase_label="Recovery",
        phase_style="theme.phase.development",
        operation_display_state=rebase.operation_display_state,
    )
    pd.update_status_bar(model)
    with pd:
        sb = pd.status_bar
        assert isinstance(sb, StatusBar)
        assert sb.is_active is True

    out = buf.getvalue()
    assert "EVIDENCE_DIAGNOSIS" in out
    assert "CONFLICT_RESOLUTION" not in out


def test_rebase_state_produces_all_eight_display_states() -> None:
    """All eight ADR-0007 operation display states are resolvable from typed fields."""
    from ralph.display.auto_integrate_message import (
        OperationDisplayState,
        format_auto_integrate_message,
    )
    from ralph.pipeline.rebase_state import RebaseState

    # 1. integrating
    st_integrating = RebaseState(last_action="integrating")
    assert st_integrating.operation_display_state == OperationDisplayState.INTEGRATING.value

    # 2. landing
    st_landing = RebaseState(last_action="rebased", fast_forwarded=True)
    assert st_landing.operation_display_state == OperationDisplayState.LANDING.value

    # 3. publishing
    st_publishing = RebaseState(last_publication_decision="publish_now")
    assert st_publishing.operation_display_state == OperationDisplayState.PUBLISHING.value

    # 4. conflict_resolution
    st_conflict = RebaseState(last_action="conflict")
    assert st_conflict.operation_display_state == OperationDisplayState.CONFLICT_RESOLUTION.value
    st_resolving_rebase = RebaseState(resolving_rebase=True)
    assert (
        st_resolving_rebase.operation_display_state
        == OperationDisplayState.CONFLICT_RESOLUTION.value
    )
    st_resolving_merge = RebaseState(resolving_merge=True)
    assert (
        st_resolving_merge.operation_display_state
        == OperationDisplayState.CONFLICT_RESOLUTION.value
    )

    # 5. evidence_diagnosis
    st_retained = RebaseState(recovery_record_retained=True)
    assert st_retained.operation_display_state == OperationDisplayState.EVIDENCE_DIAGNOSIS.value
    st_diag_owner = RebaseState(diagnostic_ownership="evidence-hash")
    assert st_diag_owner.operation_display_state == OperationDisplayState.EVIDENCE_DIAGNOSIS.value
    st_diagnosing = RebaseState(last_action="diagnosing")
    assert st_diagnosing.operation_display_state == OperationDisplayState.EVIDENCE_DIAGNOSIS.value

    # 6. waiting
    st_waiting = RebaseState(last_action="waiting")
    assert st_waiting.operation_display_state == OperationDisplayState.WAITING.value

    # 7. intervention_required
    st_intervention = RebaseState(last_decision="report_intervention_required")
    assert (
        st_intervention.operation_display_state == OperationDisplayState.INTERVENTION_REQUIRED.value
    )
    st_intervention_act = RebaseState(last_action="intervention_required")
    assert (
        st_intervention_act.operation_display_state
        == OperationDisplayState.INTERVENTION_REQUIRED.value
    )

    # 8. terminal
    st_terminal_success = RebaseState(last_decision="report_terminal_success")
    assert st_terminal_success.operation_display_state == OperationDisplayState.TERMINAL.value
    st_terminal_failure = RebaseState(last_decision="report_terminal_failure")
    assert st_terminal_failure.operation_display_state == OperationDisplayState.TERMINAL.value

    # Verify format_auto_integrate_message eliminates arbitrary prose inference
    msg_custom_prose = format_auto_integrate_message(
        action="rebase and endpoint merge both conflicted",
        target="main",
        fast_forwarded=False,
    )
    assert "rebase and endpoint merge both conflicted" in msg_custom_prose
    assert "conflict resolution" not in msg_custom_prose

    # Verify format_auto_integrate_message normalizes provided operation_display_state
    msg_normalized = format_auto_integrate_message(
        action="ignored",
        target="main",
        operation_display_state="EVIDENCE_DIAGNOSIS",
    )
    assert "evidence diagnosis (main)" in msg_normalized
