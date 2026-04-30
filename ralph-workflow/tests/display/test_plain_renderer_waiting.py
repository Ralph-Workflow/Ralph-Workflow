"""Tests for PlainLogRenderer kind-specific [waiting] tag rendering."""

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
    return PipelineSnapshot(**defaults)  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library


def test_waiting_progress_renders_info_level() -> None:
    """A PROGRESS waiting_status_line is emitted as INFO level with [waiting] tag."""
    renderer, buf = _make_renderer()
    snap = _base_snapshot(
        waiting_status_line=(
            "Background child work still active (run=60s, cumulative=120s, ceiling=1800s)"
        ),
    )
    renderer.emit_snapshot(snap)
    out = buf.getvalue()
    assert "[waiting]" in out
    assert "still active" in out
    assert "INFO" in out


def test_waiting_suspected_frozen_renders_warn_level() -> None:
    """A SUSPECTED_FROZEN waiting_status_line is emitted as WARN level with [waiting] tag."""
    renderer, buf = _make_renderer()
    snap = _base_snapshot(
        waiting_status_line=(
            "Background child work may be frozen"
            " (cumulative=600s, ceiling=1800s, evidence=time_and_workspace_quiet)"
        ),
    )
    renderer.emit_snapshot(snap)
    out = buf.getvalue()
    assert "[waiting]" in out
    assert "may be frozen" in out
    assert "WARN" in out


def test_waiting_hard_stop_renders_error_level() -> None:
    """A HARD_STOP waiting_status_line is emitted as ERROR level with [waiting] tag."""
    renderer, buf = _make_renderer()
    snap = _base_snapshot(
        waiting_status_line=(
            "Background child work hit hard ceiling"
            " (cumulative=1800s, ceiling=1800s,"
            " scoped_child_active=True, oldest_child_seconds=720s)"
        ),
    )
    renderer.emit_snapshot(snap)
    out = buf.getvalue()
    assert "[waiting]" in out
    assert "hit hard ceiling" in out
    assert "ERROR" in out


def test_waiting_line_does_not_overwrite_activity_line() -> None:
    """Both waiting_status_line and last_activity_line render as separate lines."""
    renderer, buf = _make_renderer()
    snap = _base_snapshot(
        waiting_status_line=(
            "Background child work still active (run=60s, cumulative=120s, ceiling=1800s)"
        ),
        last_activity_line="claude/sonnet tool: mcp__ralph__read_file",
        active_agent="claude/sonnet",
    )
    renderer.emit_snapshot(snap)
    out = buf.getvalue()
    assert "[waiting]" in out
    assert "[activity]" in out
    assert "still active" in out
    assert "mcp__ralph__read_file" in out


def test_waiting_none_emits_no_waiting_line() -> None:
    """No [waiting] line is emitted when waiting_status_line is None."""
    renderer, buf = _make_renderer()
    snap = _base_snapshot(waiting_status_line=None)
    renderer.emit_snapshot(snap)
    out = buf.getvalue()
    assert "[waiting]" not in out


def test_waiting_line_deduplication() -> None:
    """Identical waiting_status_line across two consecutive snapshots emits only one line."""
    renderer, buf = _make_renderer()
    line = "Background child work still active (run=60s, cumulative=120s, ceiling=1800s)"
    snap1 = _base_snapshot(waiting_status_line=line)
    snap2 = _base_snapshot(waiting_status_line=line)
    renderer.emit_snapshot(snap1)
    renderer.emit_snapshot(snap2)
    out = buf.getvalue()
    assert out.count("[waiting]") == 1
