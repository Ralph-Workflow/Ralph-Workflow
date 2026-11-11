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

_FORBIDDEN_TOKENS: tuple[str, ...] = (
    "thinking-start",
    "thinking-end",
    "thinking-continue#",
    "thinking-checkpoint#",
    "content-start",
    "content-end",
    "content-continue#",
    "content-checkpoint#",
    "\u21b3 preview:",
    "\u21b3 ai-summary:",
    "\u21b3 summary:",
)


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


def _count_lines_with_token(output: str, token: str) -> int:
    return sum(1 for line in _plain_lines(output) if token in line)


class TestStreamingBlockCoalescingSingleEntry:
    def test_thinking_block_emits_zero_lines_during_streaming(self, tmp_path: Path) -> None:
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.THINKING,
            content="First sentence.",
            metadata={},
        )
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.THINKING,
            content="Second sentence.",
            metadata={},
        )
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.THINKING,
            content="Third sentence.",
            metadata={},
        )

        out_before = buf.getvalue()
        assert "First sentence." not in out_before, (
            "Streaming layer must not emit per-fragment lines; got:\n" + out_before
        )
        assert "Second sentence." not in out_before
        assert "Third sentence." not in out_before

    def test_thinking_block_closes_with_exactly_one_entry_with_joined_passage(
        self, tmp_path: Path
    ) -> None:
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.THINKING,
            content="Investigating the elapsed time.",
            metadata={},
        )
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.THINKING,
            content="Found the recompute seam.",
            metadata={},
        )
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.THINKING,
            content="Closing the loop.",
            metadata={},
        )
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TEXT,
            content="Done.",
            metadata={},
        )

        out = buf.getvalue()
        joined = "Investigating the elapsed time. Found the recompute seam. Closing the loop."

        assert joined in out, f"Expected full joined passage in output. Output:\n{out}"
        assert out.count(joined) == 1, (
            f"Joined passage must appear exactly once, got {out.count(joined)} times. "
            f"Output:\n{out}"
        )

        thinking_lines = [line for line in _plain_lines(out) if "[reasoning][u1]" in line]
        assert len(thinking_lines) == 1, (
            f"Expected exactly 1 thinking close line, got {len(thinking_lines)}. "
            f"Lines: {thinking_lines}\nFull output:\n{out}"
        )

        close_line = thinking_lines[0]
        assert "fragments" not in close_line, (
            f"Close line must NOT report fragment count: {close_line!r}"
        )

        for forbidden in _FORBIDDEN_TOKENS:
            assert forbidden not in out, f"Forbidden token {forbidden!r} leaked into output:\n{out}"

    def test_text_block_also_coalesces_to_single_entry(self, tmp_path: Path) -> None:
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TEXT,
            content="Sentence one.",
            metadata={},
        )
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TEXT,
            content="Sentence two.",
            metadata={},
        )

        out_before = buf.getvalue()
        assert "Sentence one." not in out_before, (
            "Text streaming layer must not emit per-fragment lines; got:\n" + out_before
        )

        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TOOL_USE,
            content="bash",
            metadata={"input": {"command": "ls"}},
        )

        out = buf.getvalue()
        joined = "Sentence one. Sentence two."

        assert joined in out, f"Expected joined text passage in output:\n{out}"
        content_lines = [
            line for line in _plain_lines(out) if "[output]" in line and "[u1]" in line
        ]
        assert len(content_lines) == 1, (
            f"Expected exactly 1 content close line, got {len(content_lines)}. "
            f"Lines: {content_lines}\nFull output:\n{out}"
        )

    def test_tool_call_and_result_remain_two_separate_entries(self, tmp_path: Path) -> None:
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TOOL_USE,
            content="Bash",
            metadata={"input": {"command": "ls -la"}},
        )
        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TOOL_RESULT,
            content="file1\nfile2\n",
            metadata={},
        )

        out = buf.getvalue()
        tool_use_lines = [line for line in _plain_lines(out) if "[call][u1]" in line]
        tool_result_lines = [line for line in _plain_lines(out) if "[result][u1]" in line]
        assert len(tool_use_lines) == 1, (
            f"Expected 1 tool_use entry, got {len(tool_use_lines)}:\n{out}"
        )
        assert len(tool_result_lines) == 1, (
            f"Expected 1 tool_result entry, got {len(tool_result_lines)}:\n{out}"
        )

    def test_long_thinking_emits_one_close_line_no_checkpoints(self, tmp_path: Path) -> None:
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        for i in range(25):
            pd.emit_parsed_event(
                unit_id=unit_id,
                kind=ActivityEventKind.THINKING,
                content=f"Fragment {i:02d} of long thinking.",
                metadata={},
            )

        pd.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind.TEXT,
            content="Done.",
            metadata={},
        )

        out = buf.getvalue()
        thinking_lines = [line for line in _plain_lines(out) if "[reasoning][u1]" in line]
        assert any("⋯ reasoning" in line for line in thinking_lines), f"missing close entry:\n{out}"
        assert all("fragments" not in line for line in thinking_lines), (
            f"Close entry must NOT report fragment count: {thinking_lines!r}"
        )

        assert "checkpoint#" not in out, f"Checkpoint machinery must be retired:\n{out}"
        for forbidden in _FORBIDDEN_TOKENS:
            assert forbidden not in out, f"Forbidden token {forbidden!r} leaked:\n{out}"

    def test_no_bare_lifecycle_noise_in_output(self, tmp_path: Path) -> None:
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

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

        for line in [
            "claude/sonnet: thinking",
            "claude/sonnet: message_delta",
            "claude/sonnet: user",
        ]:
            assert line not in out, f"Bare lifecycle {line!r} leaked:\n{out}"

    @pytest.mark.parametrize(
        "provider_prefix",
        ["claude", "codex", "opencode", "gemini", "generic"],
    )
    def test_bare_lifecycle_suppressed_for_all_providers(
        self, tmp_path: Path, provider_prefix: str
    ) -> None:
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

        lines_to_suppress = [
            f"{provider_prefix}/sonnet: thinking",
            f"{provider_prefix}/sonnet: message_delta",
            f"{provider_prefix}/sonnet: user",
            f"{provider_prefix}/sonnet: assistant",
            f"{provider_prefix}/sonnet: turn.started",
            f"{provider_prefix}/sonnet: turn.completed",
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

        for line in lines_to_suppress:
            assert line not in out, f"Bare lifecycle {line!r} from {provider_prefix} leaked:\n{out}"

    @pytest.mark.parametrize(
        "provider_prefix",
        ["claude", "codex", "opencode", "gemini", "generic"],
    )
    def test_longer_sentences_with_lifecycle_tokens_not_suppressed(
        self, tmp_path: Path, provider_prefix: str
    ) -> None:
        pd, buf, _console = _make_display(tmp_path)
        unit_id = "u1"

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

        for line in lines_that_must_appear:
            assert line in out, f"Longer sentence {line!r} should NOT be suppressed but was:\n{out}"
