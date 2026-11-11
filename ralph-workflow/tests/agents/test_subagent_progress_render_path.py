from __future__ import annotations

import json
from io import StringIO
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.display import parallel_display as _pd_module
from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.activity_model import ActivityProvider
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline.activity_stream import stream_parsed_agent_activity

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


_PD_EMIT: Callable[..., object] = _pd_module.ParallelDisplay._emit_activity_event


def _make_display(tmp_path: Path) -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=2000)
    pd = ParallelDisplay(
        make_display_context(console=console, env={"CI": "1"}),
        workspace_root=tmp_path,
    )
    return pd, buf


def test_subagent_progress_event_emitted_for_tool_use(tmp_path: Path) -> None:
    pd, _buf = _make_display(tmp_path)
    captured: list[tuple[str, ActivityEventKind, str, dict[str, object]]] = []
    original_emit = _PD_EMIT

    def _capture(
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        captured.append((unit_id, kind, content or "", metadata or {}))

    def _capturing_emit(
        self: ParallelDisplay,
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
        metadata: dict[str, object] | None = None,
        timestamp: str | None = None,
    ) -> None:
        _capture(unit_id, kind, content, raw_ref, metadata)

    _pd_module.ParallelDisplay._emit_activity_event = _capturing_emit
    try:
        tool_line = json.dumps(
            {
                "type": "content_block_start",
                "content_block": {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "ls"},
                },
            }
        )
        stream_parsed_agent_activity(
            [tool_line],
            parser_type="claude",
            agent_name="claude/sonnet",
            display=pd,
        )
    finally:
        _pd_module.ParallelDisplay._emit_activity_event = original_emit

    assert [kind for _name, kind, _content, _meta in captured] == [ActivityEventKind.TOOL_USE]


_PROVIDER_TOOL_USE_LINES: dict[ActivityProvider, str] = {
    ActivityProvider.CLAUDE: json.dumps(
        {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_use",
                "name": "Bash",
                "input": {"command": "ls"},
            },
        }
    ),
    ActivityProvider.CLAUDE_INTERACTIVE: "claude tool: Read\n",
    ActivityProvider.CODEX: json.dumps(
        {
            "type": "tool_use",
            "name": "exec",
            "call_id": "call_1",
            "arguments": {"cmd": "pwd"},
        }
    ),
    ActivityProvider.CURSOR: json.dumps(
        {
            "type": "tool_call",
            "subtype": "started",
            "tool_call": {
                "mcpToolCall": {
                    "args": {
                        "toolName": "mcp__ralph__create_directory",
                        "args": {"path": "tmp/interactive-cursor-smoke"},
                    }
                },
                "toolCallId": "tool-1",
            },
        }
    ),
    ActivityProvider.OPENCODE: json.dumps(
        {
            "type": "tool_use",
            "name": "bash",
            "input": {"command": "echo hi"},
        }
    ),
    ActivityProvider.GEMINI: json.dumps(
        {
            "type": "tool_use",
            "name": "run_command",
            "args": {"command": "uptime"},
        }
    ),
    ActivityProvider.PI: json.dumps(
        {
            "type": "tool_use",
            "name": "edit",
            "input": {"path": "x.txt", "old": "a", "new": "b"},
        }
    ),
    ActivityProvider.AGY: json.dumps(
        {
            "type": "tool_use",
            "name": "shell",
            "input": {"cmd": "date"},
        }
    ),
    ActivityProvider.GENERIC: "[plain] tool: bash\n",
}


@pytest.mark.parametrize(
    "provider",
    list(_PROVIDER_TOOL_USE_LINES.keys()),
    ids=lambda p: p.value,
)
def test_subagent_progress_event_for_every_provider(
    tmp_path: Path, provider: ActivityProvider
) -> None:
    pd, _buf = _make_display(tmp_path)
    captured: list[tuple[str, ActivityEventKind, str, dict[str, object]]] = []
    original_emit = _PD_EMIT

    def _capturing_emit(
        self: ParallelDisplay,
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
        metadata: dict[str, object] | None = None,
        timestamp: str | None = None,
    ) -> None:
        captured.append((unit_id, kind, content or "", metadata or {}))

    _pd_module.ParallelDisplay._emit_activity_event = _capturing_emit
    try:
        stream_parsed_agent_activity(
            [_PROVIDER_TOOL_USE_LINES[provider]],
            parser_type=provider.value,
            agent_name=f"{provider.value}/agent",
            display=pd,
        )
    finally:
        _pd_module.ParallelDisplay._emit_activity_event = original_emit

    assert [kind for _name, kind, _content, _meta in captured] == [ActivityEventKind.TOOL_USE]
