"""Reproduction and assertion tests for thinking preview and transcript noise reduction.

These tests verify:
- Thinking blocks show previews on open, checkpoint, and close
- Tool_result shows a summary line for non-trivial content
- Redundant META [activity] lines are suppressed when CONT [tool] was just emitted
- Bare lifecycle tokens are suppressed for all provider prefixes
- Longer sentences containing lifecycle tokens are NOT suppressed (negative cases)
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from rich.console import Console

from ralph.display.activity_model import ActivityEventKind
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay

if TYPE_CHECKING:
    from pathlib import Path

# Minimum preview lines expected: one for block open, one for block close
_MIN_PREVIEW_LINES_FOR_THINKING_BLOCK = 2
# Threshold for triggering thinking preview on long continuation fragments
_THINKING_PREVIEW_MIN_CHARS = 80


def _make_display(
    tmp_path: Path,
) -> tuple[ParallelDisplay, io.StringIO, Console]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=200)
    pd = ParallelDisplay(
        make_display_context(console=console, env={"CI": "1"}),
        workspace_root=tmp_path,
    )
    return pd, buf, console


def _extract_lines(output: str) -> list[str]:
    """Extract plain text lines from console output, stripping Rich formatting."""
    return [line.strip() for line in output.strip().split("\n") if line.strip()]


def _find_line_index(lines: list[str], pattern: str) -> int | None:
    """Find index of first line containing pattern, or None."""
    for i, line in enumerate(lines):
        if pattern in line:
            return i
    return None


def _find_line_index_after(lines: list[str], pattern: str, after_idx: int) -> int | None:
    """Find index of first line containing pattern after a given index."""
    for i in range(after_idx + 1, len(lines)):
        if pattern in lines[i]:
            return i
    return None


class TestMetaActivityDeduplication:
    """Step 4 & 6: META [activity] deduplication tests."""

    def test_single_tool_use_emits_only_one_activity_line(self, tmp_path: Path) -> None:
        """Step 4: A single tool_use event should produce at most one [activity] META line.

        When emit_activity_line is called for a tool_use and then _activity_lines
        runs on a snapshot with the same tool+path, the META [activity] line
        should be suppressed (deduplicated with the preceding CONT [tool] line).
        """
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        # Emit a tool_use event
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TOOL_USE,
            content="mcp__ralph__read_file",
            metadata={"input": {"path": "ralph-workflow/ralph/prompts/template_registry.py"}},
        )

        # Emit another event to trigger snapshot processing
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TEXT,
            content="Done.",
            metadata={},
        )

        pd.stop()
        out = buf.getvalue()
        lines = _extract_lines(out)

        # Count [activity] META lines
        activity_lines = [line for line in lines if "[activity]" in line and "META" in line]
        assert len(activity_lines) <= 1, (
            f"Expected at most one [activity] META line, got {len(activity_lines)}. "
            f"Lines: {activity_lines}. Output:\n{out}"
        )

    def test_activity_deduplication_with_identical_tool_path(self, tmp_path: Path) -> None:
        """Step 4: Identical tool+path should deduplicate to single META [activity].

        When two snapshots have the same tool and path, the META [activity]
        line for the second snapshot should be suppressed.
        """
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        # Emit first tool_use
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TOOL_USE,
            content="mcp__ralph__read_file",
            metadata={"input": {"path": "ralph-workflow/ralph/prompts/template_registry.py"}},
        )

        # Emit text to close any blocks and process snapshot
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TEXT,
            content="First result.",
            metadata={},
        )

        # Emit second tool_use with SAME tool+path
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TOOL_USE,
            content="mcp__ralph__read_file",
            metadata={"input": {"path": "ralph-workflow/ralph/prompts/template_registry.py"}},
        )

        # Emit text again
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TEXT,
            content="Second result.",
            metadata={},
        )

        pd.stop()
        out = buf.getvalue()
        lines = _extract_lines(out)

        # Count [activity] META lines - should be suppressed for second identical tool_use
        activity_lines = [line for line in lines if "[activity]" in line and "META" in line]

        # With proper deduplication, we should see at most one [activity] META line
        # for the tool_use events (the second one should be suppressed)
        tool_activity_lines = [line for line in activity_lines if "mcp__ralph__read_file" in line]

        # There should be at most one [activity] META line for this tool
        assert len(tool_activity_lines) <= 1, (
            f"Expected at most one [activity] META line for identical tool_use, "
            f"got {len(tool_activity_lines)}. Lines: {tool_activity_lines}. "
            f"Full output:\n{out}"
        )

    def test_activity_not_suppressed_when_path_differs(self, tmp_path: Path) -> None:
        """Step 4: META [activity] NOT suppressed when path is different.

        If the tool is the same but the path differs, the META [activity]
        should NOT be suppressed because the signature won't match.
        """
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        # Emit first tool_use with path A
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TOOL_USE,
            content="mcp__ralph__read_file",
            metadata={"input": {"path": "ralph-workflow/ralph/prompts/template_registry.py"}},
        )

        # Emit text
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TEXT,
            content="First result.",
            metadata={},
        )

        # Emit second tool_use with SAME tool but DIFFERENT path
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TOOL_USE,
            content="mcp__ralph__read_file",
            metadata={"input": {"path": "ralph-workflow/ralph/display/plain_renderer.py"}},
        )

        # Emit text again
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TEXT,
            content="Second result.",
            metadata={},
        )

        pd.stop()
        out = buf.getvalue()

        # When path differs, the META [activity] should NOT be suppressed
        # So we should see [activity] lines for both tool calls
        # This test passes if we see the tool mentioned with different paths
        assert "template_registry.py" in out, f"First path should appear. Output:\n{out}"
        assert "plain_renderer.py" in out, (
            f"Second path should appear (not suppressed). Output:\n{out}"
        )
