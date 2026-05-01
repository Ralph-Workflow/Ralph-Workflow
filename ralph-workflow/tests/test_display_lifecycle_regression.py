"""End-to-end regression tests for PROMPT.md lifecycle noise suppression."""

from __future__ import annotations

import queue
from io import StringIO
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.display.activity_model import ActivityProvider
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.subscriber import PipelineSubscriber

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.display.snapshot import PipelineSnapshot


def _make_display(tmp_path: Path) -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=2000)
    pd = ParallelDisplay(
        make_display_context(console=console, env={"CI": "1"}),
        workspace_root=tmp_path,
    )
    return pd, buf


# The exact noisy sequence from PROMPT.md
_TOOL_LINE = (
    "claude/sonnet tool: mcp__ralph__read_file"
    " (path=ralph-workflow/ralph/prompts/template_registry.py)"
)
_PROMPT_MD_LINES: list[str] = [
    _TOOL_LINE,
    "claude/sonnet: message_delta",
    "claude/sonnet: user",
    "claude/sonnet: system (status=requesting)",
    "claude/sonnet ✗: unsupported content block type 'thinking' in agent output",
    "claude/sonnet: thinking",
    "claude/sonnet: thinking",
    "claude/sonnet: thinking",
    "claude/sonnet: thinking",
]


def test_prompt_md_sequence_produces_no_lifecycle_noise(tmp_path: Path) -> None:
    """PROMPT.md noisy transcript replay must produce zero lifecycle noise in output."""
    pd, buf = _make_display(tmp_path)
    for line in _PROMPT_MD_LINES:
        pd.activity_router.push_raw_line(
            "main",
            line,
            provider=ActivityProvider.CLAUDE,
        )
    pd.stop()
    out = buf.getvalue()

    # Bare lifecycle tokens must not appear
    assert "claude/sonnet: thinking" not in out, (
        f"Lifecycle token 'claude/sonnet: thinking' leaked into output:\n{out}"
    )
    assert "claude/sonnet: message_delta" not in out, (
        f"Lifecycle token 'claude/sonnet: message_delta' leaked into output:\n{out}"
    )
    assert "claude/sonnet: user" not in out, (
        f"Lifecycle token 'claude/sonnet: user' leaked into output:\n{out}"
    )
    assert "claude/sonnet: system (status=requesting)" not in out, (
        f"Lifecycle token 'system (status=requesting)' leaked into output:\n{out}"
    )

    # The tool call must appear with its path
    assert "mcp__ralph__read_file" in out or "ralph.read_file" in out, (
        f"Tool call missing from output:\n{out}"
    )
    assert "path=ralph-workflow/ralph/prompts/template_registry.py" in out, (
        f"Tool path missing from output:\n{out}"
    )

    # The error line must appear exactly once (it's a real error, not lifecycle noise)
    error_count = out.count("unsupported content block type 'thinking'")
    assert error_count == 1, (
        f"Expected exactly 1 error occurrence, got {error_count}:\n{out}"
    )


def test_snapshot_last_activity_line_never_stores_lifecycle(tmp_path: Path) -> None:
    """PipelineSubscriber.record_activity must not store bare lifecycle markers."""
    from unittest.mock import MagicMock  # noqa: PLC0415

    q: queue.Queue[PipelineSnapshot] = queue.Queue(maxsize=64)
    subscriber = PipelineSubscriber(
        queue=q,
        workspace_root=tmp_path,
        run_id="test-run",
    )

    state = MagicMock()
    state.phase = "development"
    state.budget_caps = {"iteration": 1}
    state.outer_progress = {"iteration": 1}
    state.review_outcome = None
    state.interrupted_by_user = False
    state.last_error = None
    state.pr_url = None
    state.push_count = 0
    state.metrics.total_agent_calls = 0
    state.metrics.total_continuations = 0
    state.metrics.total_fallbacks = 0
    state.metrics.total_retries = 0
    state.worker_states = {}
    state.work_units = []
    state.previous_phase = None
    state.current_agent = MagicMock(return_value="claude/sonnet")

    # Feed a meaningful activity first
    subscriber.notify(state)
    subscriber.record_activity(
        unit_id="main",
        agent_name="claude/sonnet",
        line="claude/sonnet tool: mcp__ralph__read_file (path=x.py)",
        tool_name="mcp__ralph__read_file",
    )
    snapshot = subscriber.build_snapshot(state)
    assert snapshot is not None
    previous_line = snapshot.last_activity_line
    assert previous_line is not None, "Expected a meaningful activity line"

    # Now feed bare lifecycle markers — they must NOT overwrite the previous line
    for lifecycle_line in [
        "claude/sonnet: thinking",
        "claude/sonnet: message_delta",
        "claude/sonnet: user",
        "claude/sonnet: system (status=requesting)",
        "thinking",
        "message_delta",
    ]:
        subscriber.record_activity(
            unit_id="main",
            agent_name="claude/sonnet",
            line=lifecycle_line,
        )
        snapshot = subscriber.build_snapshot(state)
        assert snapshot is not None
        assert snapshot.last_activity_line == previous_line, (
            f"Lifecycle line {lifecycle_line!r} overwrote last_activity_line. "
            f"Expected {previous_line!r}, got {snapshot.last_activity_line!r}"
        )


@pytest.mark.parametrize(
    "lifecycle_line",
    [
        "claude/sonnet: thinking",
        "claude/sonnet: message_delta",
        "claude/sonnet: user",
        "claude/sonnet: system (status=requesting)",
        "system (status=requesting)",
        "thinking",
        "message_delta",
        "user",
        "assistant",
        "message_start",
        "message_stop",
    ],
)
def test_is_bare_lifecycle_smoke(lifecycle_line: str) -> None:
    """is_bare_lifecycle must return True for all known lifecycle markers."""
    from ralph.display.lifecycle_filter import is_bare_lifecycle  # noqa: PLC0415

    assert is_bare_lifecycle(lifecycle_line), (
        f"Expected is_bare_lifecycle({lifecycle_line!r}) to be True"
    )


@pytest.mark.parametrize(
    "non_lifecycle_line",
    [
        "claude/sonnet: hello world",
        "claude/sonnet tool: mcp__ralph__read_file (path=x.py)",
        "claude/sonnet ✗: unsupported content block type 'thinking' in agent output",
        "some real content here",
        "",
    ],
)
def test_is_bare_lifecycle_passes_real_content(non_lifecycle_line: str) -> None:
    """is_bare_lifecycle must return False for real content lines."""
    from ralph.display.lifecycle_filter import is_bare_lifecycle  # noqa: PLC0415

    assert not is_bare_lifecycle(non_lifecycle_line), (
        f"Expected is_bare_lifecycle({non_lifecycle_line!r}) to be False"
    )
