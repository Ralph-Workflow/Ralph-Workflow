"""End-to-end regression tests for transcript shape after S-7.

S-7 (wt-028-display P1): one entry per event in the live log.

The pre-S-7 transcript duplicated streaming content up to four times
(per-fragment lines + open preview + close summary + ai-summary), and
the per-fragment lines carried internal vocabulary
(``[output][activity]``, ``message_delta`` / ``status=requesting``
lifecycle tokens, etc.). The defect shape looked like:

  INFO CONT [output][activity] claude/sonnet tool: mcp__ralph__read_file ...
  INFO META [activity] agent=claude/sonnet tool=mcp__ralph__read_file
  INFO META [activity-line] claude/sonnet tool: mcp__ralph__read_file ...
  INFO CONT [output][activity] claude/sonnet: message_delta
  INFO CONT [output][activity] claude/sonnet: thinking

Post-S-7, these patterns cannot surface because:

* the streaming layer is silent during open / continue, so per-fragment
  emissions are impossible;
* the close path emits exactly one entry per block, so per-block
  duplication is impossible;
* bare lifecycle tokens still go through the lifecycle filter.

This file pins the post-S-7 shape end-to-end against real
``ActivityRouter`` input (no test stubs that would let a regression
slip through the seams).
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


_FORBIDDEN_INTERNAL_TOKENS: tuple[str, ...] = (
    "[output][activity]",
    "[output][main]",
    "[thinking-start]",
    "[thinking-end]",
    "[thinking-continue#",
    "[thinking-checkpoint#",
    "[content-start]",
    "[content-end]",
    "[content-continue#",
    "[content-checkpoint#",
    "\u21b3 preview:",
    "\u21b3 ai-summary:",
    "\u21b3 summary:",
)


def _make_display(tmp_path: Path) -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=2000)
    pd = ParallelDisplay(
        make_display_context(console=console, env={"CI": "1"}),
        workspace_root=tmp_path,
    )
    return pd, buf


def _assert_no_internal_vocabulary(out: str) -> None:
    for token in _FORBIDDEN_INTERNAL_TOKENS:
        assert token not in out, (
            f"Internal-vocabulary token {token!r} leaked into transcript:\n{out}"
        )


def test_bare_lifecycle_tokens_produce_no_content_activity_lines(tmp_path: Path) -> None:
    """Lifecycle tokens must produce zero output lines (still suppressed)."""
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

    _assert_no_internal_vocabulary(out)
    for token in ("message_delta", "status=requesting"):
        assert token not in out, (
            f"lifecycle token {token!r} leaked into output:\n{out}"
        )


def test_tool_use_emits_one_line_with_tool_name_and_path(tmp_path: Path) -> None:
    """A tool_use event must produce exactly one [call] line containing tool name and path=."""
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
    _assert_no_internal_vocabulary(out)

    # Exactly one [call] entry — no duplicate activity.
    tool_lines = [line for line in out.splitlines() if "[call][main]" in line]
    assert len(tool_lines) == 1, (
        f"Expected exactly 1 [call] entry, got {len(tool_lines)}:\n{out}"
    )


def test_lifecycle_and_tool_use_together_produce_clean_output(tmp_path: Path) -> None:
    """Interleaved lifecycle tokens and tool_use: only the tool line surfaces."""
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
    _assert_no_internal_vocabulary(out)
    assert "message_delta" not in out, (
        f"lifecycle token 'message_delta' leaked into:\n{out}"
    )
    assert "status=requesting" not in out, (
        f"lifecycle token leaked into:\n{out}"
    )


def test_thinking_block_emits_exactly_one_close_entry(tmp_path: Path) -> None:
    """A real thinking stream through the router closes with one coalesced entry.

    S-7 / AC-04: a multi-fragment thinking block produces exactly one
    emitted line on close. This is the end-to-end version of the unit
    test, exercising the same router the live display uses.
    """
    pd, buf = _make_display(tmp_path)

    real_delta = (
        '{"type":"content_block_delta","index":0,'
        '"delta":{"type":"thinking_delta","thinking":"first thought."}}'
    )
    real_delta_2 = (
        '{"type":"content_block_delta","index":0,'
        '"delta":{"type":"thinking_delta","thinking":"second thought."}}'
    )
    real_delta_3 = (
        '{"type":"content_block_delta","index":0,'
        '"delta":{"type":"thinking_delta","thinking":"third thought."}}'
    )
    lines = [
        '{"type":"message_start","message":{"id":"msg-3"}}',
        (
            '{"type":"content_block_start","index":0,'
            '"content_block":{"type":"thinking","thinking":""}}'
        ),
        real_delta,
        real_delta_2,
        real_delta_3,
        '{"type":"content_block_stop","index":0}',
        '{"type":"message_stop"}',
    ]

    for line in lines:
        pd.activity_router.push_raw_line("main", line, provider=ActivityProvider.CLAUDE)

    pd.stop()
    out = buf.getvalue()

    # The joined passage appears exactly once.
    joined = "first thought. second thought. third thought."
    assert joined in out, f"Joined passage missing from output:\n{out}"
    assert out.count(joined) == 1, (
        f"Joined passage must appear exactly once, got {out.count(joined)}:\n{out}"
    )

    # Exactly one thinking close entry — no per-fragment or preview lines.
    thinking_lines = [
        line for line in out.splitlines() if "[reasoning][main]" in line
    ]
    assert len(thinking_lines) == 1, (
        f"Expected exactly 1 thinking close line, got {len(thinking_lines)}:\n{out}"
    )
    _assert_no_internal_vocabulary(out)


def test_whitespace_only_thinking_delta_produces_no_thinking_output(tmp_path: Path) -> None:
    """Whitespace-only thinking delta must not produce [reasoning] output.

    A whitespace-only block has zero accumulated fragments after close
    and the close path returns early, so no [reasoning] entry surfaces.
    """
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

    assert "[think" not in out, (
        f"[think tag found for whitespace content in:\n{out}"
    )


def test_non_empty_thinking_close_entry_carries_joined_passage(tmp_path: Path) -> None:
    """A non-empty thinking stream closes with one entry carrying the joined passage."""
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

    assert "deep reasoning here" in out, (
        f"thinking content not found in:\n{out}"
    )

    # Exactly one thinking close entry.
    thinking_lines = [
        line for line in out.splitlines() if "[reasoning][main]" in line
    ]
    assert len(thinking_lines) == 1, (
        f"Expected exactly 1 thinking close line, got {len(thinking_lines)}:\n{out}"
    )
    _assert_no_internal_vocabulary(out)
