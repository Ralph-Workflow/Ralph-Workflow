"""Tests for PlainLogRenderer single canonical [activity] tag across snapshots."""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO

from rich.console import Console

from ralph.display.plain_renderer import PlainLogRenderer
from ralph.display.snapshot import PipelineSnapshot


def _make_renderer() -> tuple[PlainLogRenderer, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=200)
    return PlainLogRenderer(console), buf


def _base_snapshot(**kwargs: object) -> PipelineSnapshot:
    defaults: dict[str, object] = {
        "phase": "development",
        "previous_phase": None,
        "iteration": 1,
        "total_iterations": 3,
        "reviewer_pass": 0,
        "total_reviewer_passes": 1,
        "review_issues_found": False,
        "interrupted_by_user": False,
        "last_error": None,
        "pr_url": None,
        "push_count": 0,
        "total_agent_calls": 0,
        "total_continuations": 0,
        "total_fallbacks": 0,
        "total_retries": 0,
        "workers": (),
        "prompt_path": None,
        "prompt_preview": (),
        "run_id": None,
        "created_at": datetime.now(UTC),
    }
    defaults.update(kwargs)
    return PipelineSnapshot(**defaults)  # type: ignore[arg-type]


def test_plain_renderer_emits_single_activity_tag_across_snapshots() -> None:
    """Two-snapshot sequence for the same tool call produces exactly one [activity] line."""
    renderer, buf = _make_renderer()

    # Snapshot N: structured fields only, no last_activity_line
    snap_n = _base_snapshot(
        active_agent="claude/sonnet",
        active_tool="mcp__ralph__read_file",
        active_path="ralph/pipeline/runner.py",
        last_activity_line=None,
    )
    renderer.emit_snapshot(snap_n)
    out_n = buf.getvalue()

    activity_count_n = out_n.count("[activity]")
    assert activity_count_n == 1, f"Expected 1 [activity] in snapshot N, got {activity_count_n}"
    assert "[activity-line]" not in out_n
    assert "ralph.read_file" in out_n or "mcp__ralph__read_file" in out_n or "read_file" in out_n

    buf.truncate(0)
    buf.seek(0)

    # Snapshot N+1: same structured fields, now with last_activity_line
    activity_line = (
        "claude/sonnet tool: mcp__ralph__read_file (path=ralph/pipeline/runner.py)"
    )
    snap_n1 = _base_snapshot(
        active_agent="claude/sonnet",
        active_tool="mcp__ralph__read_file",
        active_path="ralph/pipeline/runner.py",
        last_activity_line=activity_line,
    )
    renderer.emit_snapshot(snap_n1)
    out_n1 = buf.getvalue()

    activity_count_n1 = out_n1.count("[activity]")
    assert activity_count_n1 == 1, (
        f"Expected 1 [activity] in snapshot N+1, got {activity_count_n1}. Output:\n{out_n1}"
    )
    assert "[activity-line]" not in out_n1
    # The free-form line must be preferred and contain the friendly tool name
    assert "mcp__ralph__read_file" in out_n1 or "ralph.read_file" in out_n1
    assert "ralph/pipeline/runner.py" in out_n1


def test_activity_line_content_uses_last_activity_line_when_set() -> None:
    """When last_activity_line is set, its content appears in the [activity] line."""
    renderer, buf = _make_renderer()
    snap = _base_snapshot(
        active_agent="claude/sonnet",
        last_activity_line="claude/sonnet tool: mcp__ralph__exec (command=ls)",
    )
    renderer.emit_snapshot(snap)
    out = buf.getvalue()
    assert "[activity]" in out
    assert "mcp__ralph__exec" in out
    assert "[activity-line]" not in out


def test_activity_line_uses_structured_fields_when_no_last_activity_line() -> None:
    """When last_activity_line is absent, structured key=value fields appear in [activity]."""
    renderer, buf = _make_renderer()
    snap = _base_snapshot(
        active_agent="claude/sonnet",
        active_tool="mcp__ralph__read_file",
        active_path="some/path.py",
        last_activity_line=None,
    )
    renderer.emit_snapshot(snap)
    out = buf.getvalue()
    assert "[activity]" in out
    assert "agent=claude/sonnet" in out
    assert "[activity-line]" not in out


def test_activity_line_no_duplication_when_same_snapshot_repeated() -> None:
    """Identical back-to-back snapshots emit the [activity] line only once."""
    renderer, buf = _make_renderer()
    snap = _base_snapshot(
        active_agent="claude/sonnet",
        last_activity_line="claude/sonnet tool: mcp__ralph__read_file (path=x.py)",
    )
    renderer.emit_snapshot(snap)
    renderer.emit_snapshot(snap)
    out = buf.getvalue()
    # Only one [activity] line total across both snapshots
    assert out.count("[activity]") == 1
