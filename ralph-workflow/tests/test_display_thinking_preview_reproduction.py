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

import pytest
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

    def test_thinking_preview_at_start_and_end_for_short_thinking(self, tmp_path: Path) -> None:
        """Step 6: For short thinking, previews appear at thinking-start AND thinking-end.

        With short thinking content (below checkpoint thresholds), previews appear
        at the block boundaries (start and end), not between them.
        """
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        # Open thinking
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.THINKING,
            content="Short thinking content.",
            metadata={},
        )

        # Emit more thinking content (still short, won't trigger checkpoints)
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.THINKING,
            content="More short content.",
            metadata={},
        )

        # Close thinking block with a text event
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TEXT,
            content="Done.",
            metadata={},
        )

        pd.stop()
        out = buf.getvalue()
        lines = _extract_lines(out)

        # Find indices of thinking-start and thinking-end
        start_idx = _find_line_index(lines, "thinking-start")
        end_idx = _find_line_index(lines, "thinking-end")

        assert start_idx is not None, f"thinking-start not found. Output:\n{out}"
        assert end_idx is not None, f"thinking-end not found. Output:\n{out}"
        assert start_idx < end_idx, "thinking-start must come before thinking-end"

        # Count preview lines - should have at least 2 (one at start, one at end)
        preview_count = sum(1 for line in lines if "↳ preview:" in line)
        assert preview_count >= _MIN_PREVIEW_LINES_FOR_THINKING_BLOCK, (
            f"Expected at least {_MIN_PREVIEW_LINES_FOR_THINKING_BLOCK} "
            f"↳ preview: lines (start + end), "
            f"got {preview_count}. Output:\n{out}"
        )

    def test_thinking_checkpoint_preview_for_long_thinking(self, tmp_path: Path) -> None:
        """Step 2: Long thinking content triggers checkpoint previews.

        When thinking content exceeds checkpoint thresholds (20+ fragments or 4000+ chars),
        additional preview lines appear at checkpoint boundaries.
        """
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        # Emit thinking content that will trigger checkpoints.
        # Each fragment is ~100 chars; 20+ fragments exceeds checkpoint threshold.
        base_content = "Thinking content fragment X for checkpoint trigger. "
        for i in range(25):  # 25 fragments > 20 threshold
            pd.emit_parsed_event(
                unit_id=unit_id,
                kind=ActivityEventKind.THINKING,
                content=base_content.replace("X", str(i)),
                metadata={},
            )

        # Close thinking block
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TEXT,
            content="Done.",
            metadata={},
        )

        pd.stop()
        out = buf.getvalue()
        lines = _extract_lines(out)

        # Count thinking-checkpoint#N lines - should have at least one
        checkpoint_lines = [line for line in lines if "thinking-checkpoint#" in line]
        preview_lines = [line for line in lines if "↳ preview:" in line]

        # With 25 fragments, we should hit the 20-fragment checkpoint threshold
        assert len(checkpoint_lines) > 0, (
            f"Expected at least one thinking-checkpoint# line for 25 fragments, "
            f"got {len(checkpoint_lines)}. Output:\n{out}"
        )
        # Should have more than 2 previews (start + checkpoint + end)
        # Minimum 3: start, checkpoint, and end
        assert len(preview_lines) > _MIN_PREVIEW_LINES_FOR_THINKING_BLOCK, (
            f"Expected more than {_MIN_PREVIEW_LINES_FOR_THINKING_BLOCK} "
            f"↳ preview: lines (start + checkpoint(s) + end), "
            f"got {len(preview_lines)}. Output:\n{out}"
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

    def test_tool_result_summary_appears_above_tool_result_line(self, tmp_path: Path) -> None:
        """Step 6: The ↳ summary: line appears immediately above the [tool-result] line."""
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        # Emit a tool_result with substantial multi-line content
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
        lines = _extract_lines(out)

        # Find summary line and tool-result content line separately
        summary_idx = None
        tool_result_idx = None
        for i, line in enumerate(lines):
            if "↳ summary:" in line:
                summary_idx = i
            # The actual tool-result content line has the markdown content
            elif "[tool-result]" in line and "# Template Registry" in line:
                tool_result_idx = i

        assert summary_idx is not None, f"↳ summary: line not found. Output:\n{out}"
        assert tool_result_idx is not None, f"tool-result content line not found. Output:\n{out}"

        # Summary should be immediately above tool-result (difference of 1)
        assert tool_result_idx - summary_idx == 1, (
            f"Expected ↳ summary: to be immediately above [tool-result] content, "
            f"but summary is at idx {summary_idx} and tool-result at idx {tool_result_idx} "
            f"(difference={tool_result_idx - summary_idx}). Output:\n{out}"
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

    @pytest.mark.parametrize(
        "provider_prefix",
        ["claude", "codex", "opencode", "gemini", "generic"],
    )
    def test_longer_sentences_with_lifecycle_tokens_not_suppressed(
        self, tmp_path: Path, provider_prefix: str
    ) -> None:
        """Step 5: Longer sentences containing lifecycle tokens are NOT suppressed.

        This is a negative test: sentences like 'done with analysis' or
        'ready to proceed' should NOT be suppressed even though they
        contain the tokens 'done' or 'ready'.
        """
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        # These longer sentences contain lifecycle tokens but are NOT bare tokens
        lines_that_must_appear = [
            f"{provider_prefix}/qwen: done with the analysis, moving on",
            f"{provider_prefix}/qwen: ready to proceed with implementation",
            f"{provider_prefix}/qwen: finish setup now and continue",
            f"{provider_prefix}/qwen: end of analysis follows shortly",
            f"{provider_prefix}/qwen: stop the current operation immediately",
            f"{provider_prefix}/qwen: complete the task before stopping",
            f"{provider_prefix}/qwen: starting the implementation phase",
            f"{provider_prefix}/qwen: beginning work on the solution",
        ]

        for line in lines_that_must_appear:
            pd.emit(unit_id, line)

        pd.stop()
        out = buf.getvalue()

        # These longer sentences MUST appear (not be suppressed)
        for line in lines_that_must_appear:
            assert line in out, (
                f"Longer sentence {line!r} should NOT be suppressed but was. Output:\n{out}"
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
