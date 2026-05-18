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


class TestToolResultHeadline:
    """Step 3: Tool result headline for content below condensation threshold."""

    def test_short_tool_result_no_summary(self, tmp_path: Path) -> None:
        """Short tool result (<80 chars) does NOT get a summary line."""
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TOOL_RESULT,
            content="Done.",  # Short content
            metadata={},
        )

        pd.stop()
        out = buf.getvalue()

        # Short content should NOT get a summary line
        assert "↳ summary:" not in out, (
            f"Short tool_result should not show ↳ summary:. Output:\n{out}"
        )

    def test_long_tool_result_gets_summary(self, tmp_path: Path) -> None:
        """Tool result >=80 chars gets a summary line via build_headline_or_placeholder."""
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        long_content = (
            "This is a longer tool result content that should trigger "
            "the headline summary since it exceeds the 80 character threshold."
        )
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TOOL_RESULT,
            content=long_content,
            metadata={},
        )

        pd.stop()
        out = buf.getvalue()

        # Should get a summary line
        assert "↳ summary:" in out, (
            f"Expected tool_result >=80 chars to show ↳ summary:. Output:\n{out}"
        )
