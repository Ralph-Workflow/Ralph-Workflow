"""Quiet-mode test: ParallelDisplay in is_quiet=True mode emits zero console output.

The wt-007 consolidation enforces that all 8 short-circuit-capable
public emit methods on ParallelDisplay short-circuit when the
display is constructed with ``is_quiet=True``. The two methods that
route to subscribers (``emit_parsed_event``, ``emit_analysis_result``)
correctly stay un-guarded and are excluded from this assertion.

S-7 (wt-028-display P1 / AC-07): the refined contract is that quiet
mode silences the terminal surface but still writes agent-event
records to ``.agent/raw/<safe_id>.rendered.log`` so a headless run
leaves the same audit trail as a non-quiet run. Plumbing commands
do not reach ``emit_parsed_event``, so their silence stays
absolute (no spurious record entries).
"""

from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from ralph.display._run_start_orientation import RunStartOrientation
from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.phase_lifecycle import PhaseExitModel
from ralph.display.record_writer import safe_id_for
from ralph.pipeline.worker_status import WorkerStatus


def _make_quiet_display(
    workspace_root: Path | None = None,
) -> tuple[ParallelDisplay, io.StringIO, Path]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    ctx = make_display_context(console=console, env={"CI": "1"})
    root = workspace_root if workspace_root is not None else Path("/tmp/quiet-display")
    return ParallelDisplay(ctx, is_quiet=True, workspace_root=root), buf, root


def test_quiet_mode_emits_nothing_for_lifecycle_methods() -> None:
    pd, buf, _root = _make_quiet_display()
    orientation = RunStartOrientation()
    pd.emit_run_start(orientation)
    pd.begin_phase("planning")
    pd.emit_phase_close("planning", "artifacts")
    pd.emit_phase_close_from_exit(
        PhaseExitModel(
            phase_name="planning",
            phase_role="planning",
            agent_name="planner",
            elapsed_seconds=1.0,
        )
    )
    pd.emit_run_end(phase="final")
    pd.emit("unit-1", "test log line")
    pd.set_status("unit-1", WorkerStatus.RUNNING)
    pd.record_artifact_outcome("committed")
    assert buf.getvalue() == "", f"Quiet mode must emit zero output, got:\n{buf.getvalue()!r}"


def test_quiet_mode_suppresses_file_preview_but_keeps_its_plain_record(tmp_path: Path) -> None:
    """S-3: quiet tool events preserve the ANSI-free audit projection only."""
    pd, buf, _root = _make_quiet_display(tmp_path)
    pd.emit_parsed_event(
        unit_id="claude",
        kind=ActivityEventKind.TOOL_USE,
        content="mcp__ralph__write_file",
        metadata={"input": {"path": "a.py", "content": "\x1b[31manswer = 42\x1b[0m"}},
    )
    pd.stop()
    assert buf.getvalue() == ""
    record = (tmp_path / ".agent" / "raw" / f"{safe_id_for('claude')}.rendered.log").read_text(
        encoding="utf-8"
    )
    assert "answer = 42" in record
    assert "\x1b" not in record


def test_quiet_mode_writes_agent_event_records(tmp_path: Path) -> None:
    """S-7 (AC-07): agent events still reach ``.agent/raw/<safe_id>.rendered.log``.

    The terminal surface stays silent; the file surface receives the
    same presented entry a non-quiet run would have written. The
    rendered record is a content audit trail; a headless run must
    leave the same trail as an interactive one.
    """
    pd, buf, _root = _make_quiet_display(tmp_path)
    pd.emit_parsed_event(
        unit_id="claude",
        kind=ActivityEventKind.TEXT,
        content="audit-trail event",
        metadata={},
    )
    pd.stop()
    # Terminal surface stays silent.
    assert buf.getvalue() == ""
    # File surface receives the presented entry.
    expected_path = tmp_path / ".agent" / "raw" / f"{safe_id_for('claude')}.rendered.log"
    assert expected_path.exists(), (
        f"Quiet mode must still write the rendered record; missing {expected_path}"
    )
    body = expected_path.read_text(encoding="utf-8")
    assert "audit-trail event" in body
