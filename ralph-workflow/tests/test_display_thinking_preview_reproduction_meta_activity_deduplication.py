from __future__ import annotations

import io
from typing import TYPE_CHECKING

from rich.console import Console

from ralph.display.activity_model import ActivityEventKind
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay

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


def _activity_meta_lines(output: str) -> list[str]:
    return [line for line in _plain_lines(output) if "[activity]" in line and "META" in line]


class TestStreamingBlockCoalescingNoActivityDuplication:
    def test_single_tool_use_emits_at_most_one_activity_line(self, tmp_path: Path) -> None:
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TOOL_USE,
            content="mcp__ralph__read_file",
            metadata={"input": {"path": "ralph-workflow/ralph/prompts/template_registry.py"}},
        )
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TEXT,
            content="Done.",
            metadata={},
        )

        out = buf.getvalue()
        activity_lines = _activity_meta_lines(out)
        assert len(activity_lines) <= 1, (
            f"Expected at most 1 META [activity] line, got {len(activity_lines)}: "
            f"{activity_lines}\nFull output:\n{out}"
        )

    def test_two_tool_uses_with_identical_path_emits_at_most_one_activity(
        self, tmp_path: Path
    ) -> None:
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        for _ in range(2):
            pd.emit_parsed_event(
                unit_id=unit_id,
                kind=ActivityEventKind.TOOL_USE,
                content="mcp__ralph__read_file",
                metadata={"input": {"path": "ralph-workflow/ralph/prompts/template_registry.py"}},
            )
            pd.emit_parsed_event(
                unit_id=unit_id,
                kind=ActivityEventKind.TEXT,
                content="result.",
                metadata={},
            )

        out = buf.getvalue()
        tool_activity_lines = [
            line for line in _activity_meta_lines(out) if "mcp__ralph__read_file" in line
        ]
        assert len(tool_activity_lines) <= 1, (
            f"Expected at most 1 [activity] META line for the tool, got "
            f"{len(tool_activity_lines)}: {tool_activity_lines}\nFull output:\n{out}"
        )

    def test_tool_use_with_different_paths_preserves_both(self, tmp_path: Path) -> None:
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TOOL_USE,
            content="mcp__ralph__read_file",
            metadata={"input": {"path": "ralph-workflow/ralph/prompts/template_registry.py"}},
        )
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TEXT,
            content="first result.",
            metadata={},
        )

        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TOOL_USE,
            content="mcp__ralph__read_file",
            metadata={"input": {"path": "ralph-workflow/ralph/display/plain_renderer.py"}},
        )
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TEXT,
            content="second result.",
            metadata={},
        )

        out = buf.getvalue()

        assert "template_registry.py" in out, f"First path should appear:\n{out}"
        assert "plain_renderer.py" in out, f"Second path should appear (not suppressed):\n{out}"
