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

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.pipeline_snapshot import PipelineSnapshot


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


def _make_display(
    *, is_quiet: bool = False, width: int = 120
) -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=width)
    ctx = make_display_context(console=console, env={})
    return ParallelDisplay(ctx, is_quiet=is_quiet), buf


def test_emit_snapshot_renders_phase_line() -> None:
    """The snapshot's phase name appears in a [phase] line in the captured output."""
    pd, buf = _make_display()
    pd.emit_snapshot(_make_snapshot(phase="development"))
    pd.stop()
    output = buf.getvalue()
    assert "[phase]" in output, (
        f"snapshot missing [phase] line: {output!r}"
    )
    assert "development" in output, (
        f"snapshot missing 'development' phase name: {output!r}"
    )


def test_emit_snapshot_emits_info_meta_badge() -> None:
    """The standard INFO/META badge appears somewhere in the rendered snapshot."""
    pd, buf = _make_display()
    pd.emit_snapshot(_make_snapshot())
    pd.stop()
    output = buf.getvalue()
    assert "INFO" in output, (
        f"snapshot missing INFO badge: {output!r}"
    )
    assert "META" in output, (
        f"snapshot missing META badge: {output!r}"
    )


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
    assert "[phase]" in output, (
        f"quiet mode must still render snapshot phase line: {output!r}"
    )
    assert "development" in output, (
        f"quiet mode must still render phase name: {output!r}"
    )


def test_emit_snapshot_subscriber_wired_in_constructor() -> None:
    """The constructor wires ``on_snapshot=self.emit_snapshot`` on the subscriber.

    Pin Update wt-028-display: the consolidated subscriber path must
    call ``self.emit_snapshot`` for snapshot events, not free-function
    console.print. A regression that drops the wiring would fail this
    test because ``pd._subscriber._on_snapshot`` would no longer be
    bound to ``pd.emit_snapshot``.
    """
    pd, _buf = _make_display()
    subscriber = pd._subscriber
    handler = subscriber._on_snapshot
    assert handler is not None, (
        "PipelineSubscriber must expose an on_snapshot handler"
    )
    assert handler == pd.emit_snapshot, (
        "PipelineSubscriber.on_snapshot must be pd.emit_snapshot "
        f"so snapshot events route through the consolidated surface; got {handler!r}"
    )
