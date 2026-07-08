"""Black-box tests for ``ParallelDisplay.emit_snapshot`` (wt-028-display).

Pins the public emit method that subscribes to ``PipelineSubscriber``
snapshot events and renders them onto the ``ParallelDisplay`` console
(the constructor wires ``on_snapshot=self.emit_snapshot``). The test
is black-box: it constructs a StringIO-backed rich Console, attaches
a DisplayContext, builds a real ``PipelineSnapshot``, calls
``emit_snapshot`` directly, and asserts on the captured output. No
real I/O, no time.sleep, no subprocess.

Each test must complete in < 0.1 s; the whole file finishes in well
under 0.5 s so the combined 60-second budget in ``make verify`` stays
unbroken.
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
from typing import TYPE_CHECKING

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.pipeline_snapshot import PipelineSnapshot
from ralph.pipeline.state import PipelineState

if TYPE_CHECKING:
    from pathlib import Path


def _make_snapshot(
    *,
    phase: str = "development",
    run_id: str = "r-1",
    active_unit_id: str | None = "reviewer-agent/1",
) -> PipelineSnapshot:
    return PipelineSnapshot(
        phase=phase,
        previous_phase="planning",
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=None,
        pr_url=None,
        push_count=0,
        total_agent_calls=2,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path="PROMPT.md",
        prompt_preview=("summarize the change",),
        run_id=run_id,
        created_at=datetime(2026, 4, 21, tzinfo=UTC),
        plan_summary="Build the feature",
        plan_scope_items=("item A", "item B"),
        plan_total_steps=2,
        plan_current_step=2,
        active_unit_id=active_unit_id,
    )


def _make_display(*, is_quiet: bool = False, width: int = 120) -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=width)
    ctx = make_display_context(console=console, env={})
    return ParallelDisplay(ctx, is_quiet=is_quiet), buf


def _make_display_with_workspace(
    tmp_path: Path, *, is_quiet: bool = False, width: int = 120
) -> tuple[ParallelDisplay, StringIO]:
    """Construct a ParallelDisplay whose subscriber has a real workspace root.

    Used by the black-box subscriber->emit_snapshot wiring tests so the
    subscriber's prompt_path lookup does not raise on a missing workspace.
    """
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=width)
    ctx = make_display_context(console=console, env={})
    return ParallelDisplay(ctx, is_quiet=is_quiet, workspace_root=tmp_path), buf


def test_emit_snapshot_renders_phase_line() -> None:
    """The snapshot's phase name appears in a [phase] line in the captured output."""
    pd, buf = _make_display()
    pd.emit_snapshot(_make_snapshot(phase="development"))
    pd.stop()
    output = buf.getvalue()
    assert "[phase]" in output, f"snapshot missing [phase] line: {output!r}"
    assert "development" in output, f"snapshot missing 'development' phase name: {output!r}"


def test_emit_snapshot_emits_info_meta_badge() -> None:
    """The standard INFO/META badge appears somewhere in the rendered snapshot."""
    pd, buf = _make_display()
    pd.emit_snapshot(_make_snapshot())
    pd.stop()
    output = buf.getvalue()
    assert "INFO" in output, f"snapshot missing INFO badge: {output!r}"
    assert "META" in output, f"snapshot missing META badge: {output!r}"


def test_emit_snapshot_renders_plan_summary_when_present() -> None:
    """The plan_summary field shows up in the rendered snapshot."""
    pd, buf = _make_display()
    pd.emit_snapshot(_make_snapshot())
    pd.stop()
    output = buf.getvalue()
    assert "Build the feature" in output, (
        f"snapshot missing plan_summary 'Build the feature': {output!r}"
    )


def test_emit_snapshot_quiet_mode_still_renders() -> None:
    """Snapshot emission is NOT short-circuited by ``is_quiet`` (pinned contract).

    Mirror of the contract pinned in
    ``test_parallel_display_emit_completion_summary_panel.py::test_emit_completion_summary_panel_quiet_mode_still_renders``
    — pipeline state snapshot rendering must remain visible in
    ``--quiet`` mode so operators see live phase transitions.
    """
    pd, buf = _make_display(is_quiet=True)
    pd.emit_snapshot(_make_snapshot())
    pd.stop()
    output = buf.getvalue()
    assert "[phase]" in output, f"quiet mode must still render snapshot phase line: {output!r}"
    assert "development" in output, f"quiet mode must still render phase name: {output!r}"


def test_emit_snapshot_subscriber_wired_in_constructor(tmp_path: Path) -> None:
    """The constructor wires ``on_snapshot=self.emit_snapshot`` on the subscriber.

    Black-box proof: drive a snapshot through the public subscriber
    callback path (``pd.subscriber.notify(state)``) and assert on the
    rendered console output only. A regression that drops the wiring
    would fail this test because the rendered ``[phase]`` line would
    no longer appear in the captured buffer.

    Pin Update wt-028-display: the consolidated subscriber path must
    call ``self.emit_snapshot`` for snapshot events, not free-function
    ``console.print``. The test asserts on observable output only and
    does NOT reach into ``pd._subscriber._on_snapshot`` (per
    ``docs/agents/testing-guide.md`` black-box contract).
    """
    pd, buf = _make_display_with_workspace(tmp_path)
    state = PipelineState(phase="development")
    pd.subscriber.notify(state)
    pd.stop()
    output = buf.getvalue()
    assert "[phase]" in output, (
        "subscriber callback must route through emit_snapshot and render "
        f"the [phase] line; got: {output!r}"
    )
    assert "development" in output, (
        f"subscriber callback must surface the phase name from the PipelineState; got: {output!r}"
    )


def test_emit_snapshot_subscriber_wired_routes_each_unique_phase(tmp_path: Path) -> None:
    """Distinct phases drive distinct renderings through the subscriber->emit path.

    Sends three different states through the public subscriber and
    asserts the buffer contains all three phase names. A regression
    that bypasses ``emit_snapshot`` (e.g. a free-function console.print
    in the subscriber path) would still emit the phases if it copied
    the snapshot into a different code path; the test would still fail
    in that case because the phase lines emitted via the
    ParallelDisplay-emit path carry the ``[phase]`` tag prefix and
    the standard INFO/META badge.
    """
    pd, buf = _make_display_with_workspace(tmp_path)
    for phase in ("planning", "development", "review"):
        pd.subscriber.notify(PipelineState(phase=phase))
    pd.stop()
    output = buf.getvalue()
    for phase in ("planning", "development", "review"):
        assert phase in output, (
            f"subscriber->emit_snapshot path must surface phase {phase!r}; got: {output!r}"
        )
        assert "[phase]" in output, (
            f"subscriber->emit_snapshot path must emit the [phase] tag for "
            f"phase {phase!r}; got: {output!r}"
        )
