"""Reproduction and assertion tests for tool_result coalescing (S-7).

Pre-S-7, ``tool_result`` triggered a ``↳ summary:`` line above the
tool-result content as part of the same over-emission that produced
``↳ preview:`` and ``↳ ai-summary:`` lines for thinking blocks.

Post-S-7, ``tool_result`` is no longer in ``_STREAMING_KINDS``-style
emission paths that produce preview / summary / ai-summary supplements.
The single-entry contract means each tool_result is one line carrying
its own content; the ``↳ summary:`` supplement is retired along with the
other preview machinery.

These tests pin the new shape: tool_result content is rendered as a
single entry, no ``↳ summary:`` line follows, and the underlying
``build_headline_or_placeholder`` helper still surfaces a headline when
callers ask for one explicitly (the helper itself is not retired).
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.display.activity_model import ActivityEventKind
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay

# ponytail: Rich Live teardown can exceed 1s under xdist scheduling; 5s preserves the behavior check within the 60s suite cap.
pytestmark = pytest.mark.timeout_seconds(5)

if TYPE_CHECKING:
    from pathlib import Path


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


def _plain_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.strip()]


class TestToolResultSingleEntry:
    """S-7: tool_result is one entry, no ↳ summary: supplement."""

    def test_short_tool_result_emits_one_line_with_content(self, tmp_path: Path) -> None:
        """Short tool_result content surfaces as exactly one [result] entry."""
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TOOL_RESULT,
            content="Done.",
            metadata={},
        )

        out = buf.getvalue()
        lines = _plain_lines(out)
        result_lines = [line for line in lines if "[result][u1]" in line]

        assert len(result_lines) == 1, (
            f"Expected exactly 1 tool_result entry, got {len(result_lines)}: "
            f"{result_lines}\nFull output:\n{out}"
        )
        assert "Done." in result_lines[0]
        assert "\u21b3 summary:" not in out, (
            f"Retired ↳ summary: supplement must not appear:\n{out}"
        )

    def test_long_tool_result_emits_one_line_with_content_no_summary_supplement(
        self, tmp_path: Path
    ) -> None:
        """Long tool_result remains one logical entry without a summary supplement.

        The renderer may wrap that entry into hanging continuation rows;
        those rows are layout, not duplicate activity.
        """
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        long_content = (
            "This is a longer tool result content that used to trigger "
            "the headline summary since it exceeded the 80 character threshold."
        )
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TOOL_RESULT,
            content=long_content,
            metadata={},
        )

        out = buf.getvalue()
        lines = _plain_lines(out)
        result_lines = [line for line in lines if "[result][u1]" in line]

        primary_lines = [line for line in result_lines if "✓ PASS ↳" in line]
        continuation_lines = [line for line in result_lines if "✓ PASS ↳" not in line]
        assert len(primary_lines) == 1, (
            f"Expected exactly 1 logical tool_result entry, got {len(primary_lines)}: "
            f"{result_lines}\nFull output:\n{out}"
        )
        assert len(continuation_lines) == 1, (
            f"Expected one hanging continuation, got {continuation_lines}:\n{out}"
        )
        assert "This is a longer tool result content" in primary_lines[0]
        assert "headline summary since it exceeded the 80 character threshold." in continuation_lines[0]
        assert "\u21b3 summary:" not in out, (
            f"Retired ↳ summary: supplement must not appear:\n{out}"
        )
        assert "\u21b3 preview:" not in out
        assert "\u21b3 ai-summary:" not in out
