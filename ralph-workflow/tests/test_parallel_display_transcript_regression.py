"""End-to-end regression tests for transcript noise suppression.

Reproduces the exact user-reported pattern from the issue:
  INFO CONT [content][activity] claude/sonnet tool: mcp__ralph__read_file ...
  INFO META [activity] agent=claude/sonnet tool=mcp__ralph__read_file
  INFO META [activity-line] claude/sonnet tool: mcp__ralph__read_file ...
  INFO CONT [content][activity] claude/sonnet: message_delta
  INFO CONT [content][activity] claude/sonnet: thinking

All of these patterns must be eliminated.
"""

from __future__ import annotations

import json
from io import StringIO
from typing import TYPE_CHECKING

from rich.console import Console

from ralph.display.activity_model import ActivityProvider
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay

if TYPE_CHECKING:
    from pathlib import Path


def _make_display(tmp_path: Path) -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=2000)
    pd = ParallelDisplay(
        make_display_context(console=console, env={"CI": "1"}),
        workspace_root=tmp_path,
    )
    return pd, buf


def test_bare_lifecycle_tokens_produce_no_content_activity_lines(tmp_path: Path) -> None:
    """Lifecycle tokens from the user-reported transcript must produce zero output lines."""
    pd, buf = _make_display(tmp_path)

    lifecycle_lines = [
        "claude/sonnet: message_delta",
        "claude/sonnet: user",
        "claude/sonnet: system (status=requesting)",
        "claude/sonnet: thinking",
    ]

    for line in lifecycle_lines:
        pd.activity_router.push_raw_line("main", line, provider=ActivityProvider.CLAUDE)

    pd.stop()
    out = buf.getvalue()

    assert "[content][activity]" not in out, f"[content][activity] found in:\n{out}"
    assert "[content][main]" not in out, f"[content][main] found in:\n{out}"
    for token in ("message_delta", "status=requesting"):
        assert token not in out, f"lifecycle token '{token}' leaked into output:\n{out}"


def test_tool_use_emits_one_line_with_tool_name_and_path(tmp_path: Path) -> None:
    """A tool_use event must produce one [tool] line containing tool name and path=."""
    pd, buf = _make_display(tmp_path)

    tool_event = json.dumps(
        {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_use",
                "name": "mcp__ralph__read_file",
                "input": {"path": "ralph-workflow/ralph/prompts/template_registry.py"},
            },
        }
    )
    pd.activity_router.push_raw_line("main", tool_event, provider=ActivityProvider.CLAUDE)
    pd.stop()

    out = buf.getvalue()

    assert "ralph.read_file" in out or "mcp__ralph__read_file" in out, (
        f"tool name not found in:\n{out}"
    )
    assert "path=ralph-workflow/ralph/prompts/template_registry.py" in out, (
        f"path not found in:\n{out}"
    )
    assert "[content][activity]" not in out, f"[content][activity] found in:\n{out}"


def test_lifecycle_and_tool_use_together_produce_clean_output(tmp_path: Path) -> None:
    """Interleaved lifecycle tokens and tool_use: only tool line must be in output."""
    pd, buf = _make_display(tmp_path)

    tool_event = json.dumps(
        {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_use",
                "name": "mcp__ralph__read_file",
                "input": {"path": "ralph-workflow/ralph/prompts/template_registry.py"},
            },
        }
    )
    lines = [
        "claude/sonnet: message_delta",
        tool_event,
        "claude/sonnet: user",
        "claude/sonnet: system (status=requesting)",
    ]

    for line in lines:
        pd.activity_router.push_raw_line("main", line, provider=ActivityProvider.CLAUDE)

    pd.stop()
    out = buf.getvalue()

    assert "ralph.read_file" in out or "mcp__ralph__read_file" in out, (
        f"tool name not found in:\n{out}"
    )
    assert "[content][activity]" not in out, f"[content][activity] found in:\n{out}"
    assert "message_delta" not in out, f"lifecycle token 'message_delta' leaked into:\n{out}"
    assert "status=requesting" not in out, f"lifecycle token leaked into:\n{out}"


def test_whitespace_only_thinking_delta_produces_no_thinking_output(tmp_path: Path) -> None:
    """Whitespace-only thinking delta must not produce [thinking] output."""
    pd, buf = _make_display(tmp_path)

    ws_delta = (
        '{"type":"content_block_delta","index":0,'
        '"delta":{"type":"thinking_delta","thinking":"   "}}'
    )
    lines = [
        '{"type":"message_start","message":{"id":"msg-1"}}',
        '{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}',
        ws_delta,
        '{"type":"content_block_stop","index":0}',
        '{"type":"message_stop"}',
    ]

    for line in lines:
        pd.activity_router.push_raw_line("main", line, provider=ActivityProvider.CLAUDE)

    pd.stop()
    out = buf.getvalue()

    assert "[thinking" not in out, f"[thinking tag found for whitespace content in:\n{out}"


def test_non_empty_thinking_delta_is_emitted(tmp_path: Path) -> None:
    """Non-empty thinking content must still be emitted."""
    pd, buf = _make_display(tmp_path)

    real_delta = (
        '{"type":"content_block_delta","index":0,'
        '"delta":{"type":"thinking_delta","thinking":"deep reasoning here"}}'
    )
    lines = [
        '{"type":"message_start","message":{"id":"msg-2"}}',
        '{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}',
        real_delta,
        '{"type":"content_block_stop","index":0}',
        '{"type":"message_stop"}',
    ]

    for line in lines:
        pd.activity_router.push_raw_line("main", line, provider=ActivityProvider.CLAUDE)

    pd.stop()
    out = buf.getvalue()

    assert "[thinking" in out, f"[thinking tag not found for real thinking in:\n{out}"
    assert "deep reasoning here" in out, f"thinking content not found in:\n{out}"
