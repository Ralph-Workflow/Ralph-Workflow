from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.snapshot import PipelineSnapshot


def _make_display() -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=200)
    return ParallelDisplay(make_display_context(console=console, env={})), buf


def _base_snapshot(**kwargs: object) -> PipelineSnapshot:
    defaults: dict[str, object] = {
        "phase": "development",
        "previous_phase": None,
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
    return PipelineSnapshot(**defaults)


def test_plain_renderer_emits_single_activity_tag_across_snapshots() -> None:
    pd, buf = _make_display()

    snap_n = _base_snapshot(
        active_agent="claude/sonnet",
        active_tool="mcp__ralph__read_file",
        active_path="ralph/pipeline/runner.py",
        last_activity_line=None,
    )
    pd.emit_snapshot(snap_n)

    activity_line = "claude/sonnet tool: mcp__ralph__read_file (path=ralph/pipeline/runner.py)"
    snap_n1 = _base_snapshot(
        active_agent="claude/sonnet",
        active_tool="mcp__ralph__read_file",
        active_path="ralph/pipeline/runner.py",
        last_activity_line=activity_line,
    )
    pd.emit_snapshot(snap_n1)

    out = buf.getvalue()
    activity_count = out.count("[activity]")
    assert activity_count == 1, (
        f"Expected exactly 1 [activity] line across both snapshots, got {activity_count}. "
        f"Output:\n{out}"
    )
    assert "[activity-line]" not in out, f"[activity-line] must not appear:\n{out}"
    assert "mcp__ralph__read_file" in out or "ralph.read_file" in out
    assert "ralph/pipeline/runner.py" in out


def test_activity_line_content_uses_last_activity_line_when_set() -> None:
    pd, buf = _make_display()
    snap = _base_snapshot(
        active_agent="claude/sonnet",
        last_activity_line="claude/sonnet tool: mcp__ralph__exec (command=ls)",
    )
    pd.emit_snapshot(snap)
    out = buf.getvalue()
    assert "[activity]" in out
    assert "mcp__ralph__exec" in out
    assert "[activity-line]" not in out


def test_activity_line_uses_structured_fields_when_no_last_activity_line() -> None:
    pd, buf = _make_display()
    snap = _base_snapshot(
        active_agent="claude/sonnet",
        active_tool="mcp__ralph__read_file",
        active_path="some/path.py",
        last_activity_line=None,
    )
    pd.emit_snapshot(snap)
    out = buf.getvalue()
    assert "[activity]" in out
    assert "agent=claude/sonnet" in out
    assert "[activity-line]" not in out


def test_activity_line_no_duplication_when_same_snapshot_repeated() -> None:
    pd, buf = _make_display()
    snap = _base_snapshot(
        active_agent="claude/sonnet",
        last_activity_line="claude/sonnet tool: mcp__ralph__read_file (path=x.py)",
    )
    pd.emit_snapshot(snap)
    pd.emit_snapshot(snap)
    out = buf.getvalue()
    assert out.count("[activity]") == 1


def test_repeated_tool_call_keeps_status_fresh_with_count() -> None:
    pd, buf = _make_display()
    for n in (1, 2, 3):
        snap = _base_snapshot(
            active_agent="opencode/minimax",
            active_tool="mcp__ralph__exec",
            active_path="ralph/x.py",
            active_tool_repeat=n,
            last_activity_line="opencode/minimax tool: exec (path=ralph/x.py)",
        )
        pd.emit_snapshot(snap)

    out = buf.getvalue()
    assert "x2" in out, f"second identical call must refresh status with x2:\n{out}"
    assert "x3" in out, f"third identical call must refresh status with x3:\n{out}"


def test_same_repeat_count_is_still_deduplicated() -> None:
    pd, buf = _make_display()
    snap = _base_snapshot(
        active_agent="opencode/minimax",
        active_tool="mcp__ralph__exec",
        active_path="ralph/x.py",
        active_tool_repeat=2,
        last_activity_line="opencode/minimax tool: exec (path=ralph/x.py)",
    )
    pd.emit_snapshot(snap)
    pd.emit_snapshot(snap)
    out = buf.getvalue()
    assert out.count("[activity]") == 1, f"re-render must not duplicate:\n{out}"
