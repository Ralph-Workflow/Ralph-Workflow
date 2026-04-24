"""Reproduction and assertion tests for thinking preview and transcript noise reduction.

These tests verify:
- Thinking blocks show previews on open, checkpoint, and close
- Tool_result shows a summary line for non-trivial content
- Redundant META [activity] lines are suppressed when CONT [tool] was just emitted
- Bare lifecycle tokens are suppressed for all provider prefixes
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.display.activity_model import ActivityEventKind
from ralph.display.parallel_display import ParallelDisplay

if TYPE_CHECKING:
    from pathlib import Path

# Minimum preview lines expected: one for block open, one for block close
_MIN_PREVIEW_LINES_FOR_THINKING_BLOCK = 2


def _make_display(
    tmp_path: Path,
) -> tuple[ParallelDisplay, io.StringIO, Console]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=200)
    pd = ParallelDisplay(console, {"CI": "1"}, workspace_root=tmp_path)
    return pd, buf, console


class TestThinkingPreviewAndTranscriptCleanup:
    """Verify improved transcript output per the implementation plan."""

    def test_thinking_block_shows_preview_on_open(self, tmp_path: Path) -> None:
        """Step 2: Thinking block open emits a preview line."""
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.THINKING,
            content="Let me investigate the codebase structure first.",
            metadata={},
        )

        pd.stop()
        out = buf.getvalue()

        # Should show ↳ preview: on thinking-start
        assert "↳ preview:" in out, (
            f"Expected thinking block open to show ↳ preview:. Output:\n{out}"
        )
        # Should contain thinking-start tag
        assert "thinking-start" in out, f"Expected thinking-start tag. Output:\n{out}"

    def test_thinking_block_shows_preview_on_close(self, tmp_path: Path) -> None:
        """Step 2: Thinking block close emits a preview line."""
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        # Open thinking
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.THINKING,
            content="Investigating the codebase.",
            metadata={},
        )

        # Emit a non-thinking event to close the thinking block
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TEXT,
            content="Done thinking.",
            metadata={},
        )

        pd.stop()
        out = buf.getvalue()

        # Should have thinking-end with ↳ preview:
        assert "thinking-end" in out, f"Expected thinking-end. Output:\n{out}"
        # The preview on close shows what was accumulated
        preview_count = out.count("↳ preview:")
        min_expected = _MIN_PREVIEW_LINES_FOR_THINKING_BLOCK
        assert preview_count >= min_expected, (
            f"Expected at least {min_expected} ↳ preview: lines (open + close), "
            f"got {preview_count}. Output:\n{out}"
        )

    def test_tool_result_shows_summary_for_nontrivial_content(self, tmp_path: Path) -> None:
        """Step 3: Tool result with >=80 chars shows a summary line."""
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        # Emit a tool_result with substantial content (>=80 chars)
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TOOL_RESULT,
            content=(
                "# Template Registry\n\n"
                "This module manages prompt templates.\n\n"
                "def register_template(name: str, template: str) -> None:\n"
                "    pass\n"
            ),
            metadata={},
        )

        pd.stop()
        out = buf.getvalue()

        # Should show ↳ summary: above the tool-result
        assert "↳ summary:" in out, (
            f"Expected tool_result to show ↳ summary: for substantial content. Output:\n{out}"
        )
        # Should have tool-result tag
        assert "tool-result" in out or "tool_result" in out, (
            f"Expected tool-result in output. Output:\n{out}"
        )

    def test_no_bare_lifecycle_noise_in_output(self, tmp_path: Path) -> None:
        """Verify bare lifecycle tokens don't appear in output."""
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        # Emit bare lifecycle lines via emit() (raw path)
        for line in [
            "claude/sonnet: thinking",
            "claude/sonnet: message_delta",
            "claude/sonnet: user",
            "thinking",
            "message_delta",
            "user",
        ]:
            pd.emit(unit_id, line)

        pd.stop()
        out = buf.getvalue()

        # These bare lifecycle tokens must NOT appear
        assert "claude/sonnet: thinking" not in out, (
            f"Bare lifecycle 'claude/sonnet: thinking' leaked. Output:\n{out}"
        )
        assert "claude/sonnet: message_delta" not in out, (
            f"Bare lifecycle 'claude/sonnet: message_delta' leaked. Output:\n{out}"
        )
        assert "claude/sonnet: user" not in out, (
            f"Bare lifecycle 'claude/sonnet: user' leaked. Output:\n{out}"
        )

    @pytest.mark.parametrize(
        "provider_prefix",
        ["claude", "codex", "opencode", "gemini", "generic"],
    )
    def test_bare_lifecycle_suppressed_for_all_providers(
        self, tmp_path: Path, provider_prefix: str
    ) -> None:
        """Step 5: Each provider prefix suppresses its bare lifecycle tokens."""
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        # Emit bare lifecycle lines for each provider
        lines_to_suppress = [
            f"{provider_prefix}/sonnet: thinking",
            f"{provider_prefix}/sonnet: message_delta",
            f"{provider_prefix}/sonnet: user",
            f"{provider_prefix}/sonnet: assistant",
            f"{provider_prefix}/sonnet: turn.started",
            f"{provider_prefix}/sonnet: turn.completed",
            f"{provider_prefix}/sonnet: thread.started",
            f"{provider_prefix}/sonnet: done",
            f"{provider_prefix}/sonnet: complete",
            f"{provider_prefix}/sonnet: stop",
            f"{provider_prefix}/sonnet: message_start",
            f"{provider_prefix}/sonnet: message_stop",
            f"{provider_prefix}/sonnet: content_block_start",
            f"{provider_prefix}/sonnet: content_block_stop",
        ]

        for line in lines_to_suppress:
            pd.emit(unit_id, line)

        pd.stop()
        out = buf.getvalue()

        # None of these should appear in output
        for line in lines_to_suppress:
            assert line not in out, (
                f"Bare lifecycle {line!r} from {provider_prefix} leaked. Output:\n{out}"
            )

    def test_no_bare_thinking_content_in_output(self, tmp_path: Path) -> None:
        """Step 6: No bare 'claude/sonnet: thinking' content appears."""
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        # Emit via emit() which passes through lifecycle filter
        pd.emit(unit_id, "claude/sonnet: thinking")

        pd.stop()
        out = buf.getvalue()

        # The exact bare 'claude/sonnet: thinking' must not appear
        assert "claude/sonnet: thinking" not in out, (
            f"Bare 'claude/sonnet: thinking' content appeared. Output:\n{out}"
        )


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
