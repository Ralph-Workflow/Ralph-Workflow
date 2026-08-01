"""Tests for ParallelDisplay activity_router content routing to plain pd."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from io import StringIO
from typing import TYPE_CHECKING

from rich.console import Console

from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.display.activity_model import ActivityEventKind, ActivityProvider
from ralph.display.activity_router import ActivityRouter
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.snapshot import PipelineSnapshot
from ralph.pipeline.activity_stream import stream_parsed_agent_activity
from ralph.pipeline.work_units import WorkUnit

if TYPE_CHECKING:
    from pathlib import Path

_LONG_TEXT_LEN = 5000


def _make_display(tmp_path: Path, width: int = 2000) -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=width)
    pd = ParallelDisplay(
        make_display_context(console=console, env={"CI": "1"}),
        workspace_root=tmp_path,
    )
    return pd, buf


def test_push_text_line_emits_content_tag(tmp_path: Path) -> None:
    pd, buf = _make_display(tmp_path)
    pd.activity_router.push_raw_line(
        "u",
        '{"type":"content_block_delta","delta":{"type":"text_delta","text":"hello world"}}',
        provider=ActivityProvider.CLAUDE,
    )
    # S-7: streaming layer is silent during open/continue; pd.stop() flushes
    # the open block so the single coalesced entry surfaces.
    pd.stop()
    out = buf.getvalue()
    # S-7 single-entry shape: one [output] line carrying the joined passage.
    assert "[output" in out
    assert "[u]" in out
    assert "hello world" in out
    content_lines = [line for line in out.splitlines() if "[output][u]" in line]
    assert len(content_lines) == 1, (
        f"Expected exactly 1 [output] entry on close, got {len(content_lines)}:\n{out}"
    )


def test_thinking_delta_emits_thinking_tag(tmp_path: Path) -> None:
    pd, buf = _make_display(tmp_path)
    lines = [
        '{"type":"message_start","message":{"id":"msg-1"}}',
        (
            '{"type":"content_block_start","index":0,'
            '"content_block":{"type":"thinking","thinking":""}}'
        ),
        (
            '{"type":"content_block_delta","index":0,'
            '"delta":{"type":"thinking_delta","thinking":"deep thought"}}'
        ),
        '{"type":"content_block_stop","index":0}',
        '{"type":"message_stop"}',
    ]
    for line in lines:
        pd.activity_router.push_raw_line("u", line, provider=ActivityProvider.CLAUDE)
    # S-7: stop() flushes the still-open thinking block as one coalesced entry.
    pd.stop()
    out = buf.getvalue()
    assert "[reasoning" in out
    assert "[u]" in out
    assert "deep thought" in out
    thinking_lines = [line for line in out.splitlines() if "[reasoning][u]" in line]
    assert len(thinking_lines) == 1, (
        f"Expected exactly 1 [reasoning] entry on close, got {len(thinking_lines)}:\n{out}"
    )


def test_output_does_not_contain_raw_json(tmp_path: Path) -> None:
    pd, buf = _make_display(tmp_path)
    raw_json = '{"type":"content_block_delta","delta":{"type":"text_delta","text":"hi"}}'
    pd.activity_router.push_raw_line("u", raw_json, provider=ActivityProvider.CLAUDE)
    out = buf.getvalue()
    assert raw_json not in out


def test_very_long_line_is_condensed(tmp_path: Path) -> None:
    pd, buf = _make_display(tmp_path, width=10000)

    long_text = "A" * _LONG_TEXT_LEN
    raw_json = json.dumps(
        {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": long_text},
        }
    )
    pd.activity_router.push_raw_line("u", raw_json, provider=ActivityProvider.CLAUDE)
    pd.activity_router.push_raw_line(
        "u", '{"type":"message_stop"}', provider=ActivityProvider.CLAUDE
    )
    # S-7: stop() flushes the still-open block; condensation is part of the
    # single coalesced entry.
    pd.stop()

    out = buf.getvalue()
    # S-7 single-entry shape: one [output] line carrying the condensed passage.
    assert "[output" in out
    # Content should be condensed (not all characters present)
    assert len(out) < _LONG_TEXT_LEN
    assert "…" in out or "truncated" in out or "raw unavailable" in out
    content_lines = [line for line in out.splitlines() if "[output][u]" in line]
    assert len(content_lines) == 1, (
        f"Expected exactly 1 [output] entry on close, got {len(content_lines)}:\n{out}"
    )


def test_only_one_activity_router_per_parallel_display(tmp_path: Path) -> None:
    pd, _ = _make_display(tmp_path)
    router1 = pd.activity_router
    router2 = pd.activity_router
    assert router1 is router2


def test_malformed_ndjson_does_not_crash(tmp_path: Path) -> None:
    pd, buf = _make_display(tmp_path)
    pd.activity_router.push_raw_line("u", "not valid json {{{", provider=ActivityProvider.CLAUDE)
    out = buf.getvalue()
    assert isinstance(out, str)


def test_raw_log_written_via_subprocess_executor(tmp_path: Path) -> None:
    """SubprocessAgentExecutor writes raw lines to .agent/raw/<unit>.log."""

    received: list[str] = []

    router = ActivityRouter(
        on_event=lambda uid, kind, content, ref, meta: received.append(content or "")
    )

    executor = SubprocessAgentExecutor(
        [
            "python",
            "-c",
            (
                'print(\'{"type":"content_block_delta",'
                '"delta":{"type":"text_delta","text":"exec_test"}}\')'
            ),
        ],
        activity_router=router,
        raw_overflow_root=tmp_path,
    )

    unit = WorkUnit(unit_id="unit-exec", description="test", dependencies=frozenset())

    async def run() -> None:
        await executor.run(
            unit,
            on_output=lambda line: None,
            on_status=lambda s: None,
        )

    asyncio.run(run())
    # RFC-013 P1: raw overflow log uses block buffering. drop_unit()
    # flushes the buffered tail to disk before the assertion reads it.
    executor.drop_unit("unit-exec")

    raw_log = tmp_path / ".agent" / "raw" / "unit-exec.log"
    assert raw_log.exists(), "Raw log file should be created"
    content = raw_log.read_text(encoding="utf-8")
    assert "exec_test" in content or "text_delta" in content


def test_condensed_ref_appears_in_output_with_overflow_root(tmp_path: Path) -> None:
    """When content is condensed, overflow file path appears in the output."""
    pd, buf = _make_display(tmp_path, width=10000)

    # 500 chars: above soft_limit(400), below hard_limit(4000)
    # condenser produces head + " … (truncated, see .agent/raw/u.log)"
    medium_text = "B" * 500
    raw_json = json.dumps(
        {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": medium_text},
        }
    )
    pd.activity_router.push_raw_line(
        "u",
        raw_json,
        provider=ActivityProvider.CLAUDE,
        raw_reference=".agent/raw/u.log",
    )
    # S-7: stop() flushes the still-open block; condensation lives in the
    # single coalesced entry that surfaces.
    pd.stop()

    out = buf.getvalue()
    assert "[output" in out
    assert ".agent/raw/u.log" in out
    content_lines = [line for line in out.splitlines() if "[output][u]" in line]
    assert len(content_lines) == 1, (
        f"Expected exactly 1 [output] entry on close, got {len(content_lines)}:\n{out}"
    )


def test_tool_use_input_metadata_is_surfaced_on_rendered_line(tmp_path: Path) -> None:
    """tool_use with input metadata renders path= on the [call] line."""
    pd, buf = _make_display(tmp_path)
    event = json.dumps(
        {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_use",
                "name": "mcp__ralph__read_file",
                "input": {"path": "ralph-workflow/ralph/x.py"},
            },
        }
    )
    pd.activity_router.push_raw_line("u", event, provider=ActivityProvider.CLAUDE)
    out = buf.getvalue()
    assert "ralph.read_file" in out
    assert "path=ralph-workflow/ralph/x.py" in out


def test_activity_snapshot_does_not_duplicate_activity_line(tmp_path: Path) -> None:
    """Snapshot with active_tool + last_activity_line emits exactly ONE [activity] tagged line."""

    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=200)
    renderer = ParallelDisplay(make_display_context(console=console, env={}))

    snapshot = PipelineSnapshot(
        phase="development",
        previous_phase=None,
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=None,
        pr_url=None,
        push_count=0,
        total_agent_calls=0,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path=None,
        prompt_preview=(),
        run_id=None,
        created_at=datetime.now(UTC),
        active_tool="mcp__ralph__read_file",
        last_activity_line="claude/sonnet tool: mcp__ralph__read_file (path=x.py)",
    )
    renderer.emit_snapshot(snapshot)
    out = buf.getvalue()

    # Exactly one [activity] line; no [activity-line] tag
    activity_count = out.count("[activity]")
    assert "[activity-line]" not in out, f"[activity-line] tag must not appear:\n{out}"
    assert activity_count == 1, f"Expected 1 [activity] line, got {activity_count}. Output:\n{out}"


def test_lifecycle_thinking_prefix_is_suppressed_end_to_end(tmp_path: Path) -> None:
    """Lifecycle prefix 'claude/sonnet: thinking' must not produce [output] output."""
    pd, buf = _make_display(tmp_path)
    pd.activity_router.push_raw_line(
        "main",
        "claude/sonnet: thinking",
        provider=ActivityProvider.CLAUDE,
    )
    pd.stop()
    out = buf.getvalue()
    assert "[output][main]" not in out
    assert "[reasoning][main]" not in out


def test_emit_parsed_event_drops_bare_lifecycle_structured_content(tmp_path: Path) -> None:
    """emit_parsed_event with LIFECYCLE kind and bare lifecycle content emits nothing."""

    pd, buf = _make_display(tmp_path)
    pd.emit_parsed_event("main", ActivityEventKind.LIFECYCLE, "claude/sonnet: thinking", {})
    pd.emit_parsed_event("main", ActivityEventKind.LIFECYCLE, "system (status=requesting)", {})
    pd.emit_parsed_event("main", ActivityEventKind.LIFECYCLE, "message_delta", {})
    pd.stop()
    out = buf.getvalue()
    assert "[status-content][main]" not in out
    assert "system (status=requesting)" not in out
    assert "message_delta" not in out


def test_emit_parsed_event_passes_through_non_lifecycle_content(tmp_path: Path) -> None:
    """emit_parsed_event with TEXT kind and real content renders normally."""

    pd, buf = _make_display(tmp_path)
    pd.emit_parsed_event("main", ActivityEventKind.TEXT, "actual agent output here", {})
    pd.stop()
    out = buf.getvalue()
    assert "actual agent output here" in out


def test_stream_parsed_agent_activity_thinking_routes_to_structured_path(tmp_path: Path) -> None:
    """_stream_parsed_agent_activity must not emit [output][activity] for thinking events."""

    pd, buf = _make_display(tmp_path)

    thinking_line = json.dumps(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": ""},
        }
    )
    thinking_delta = json.dumps(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "deep reasoning here"},
        }
    )
    stop_line = json.dumps({"type": "content_block_stop", "index": 0})

    stream_parsed_agent_activity(
        [thinking_line, thinking_delta, stop_line],
        parser_type="claude",
        agent_name="claude/sonnet",
        display=pd,
    )
    # S-7: stop() flushes the still-open thinking block as one coalesced entry.
    pd.stop()

    out = buf.getvalue()
    assert "[output][activity]" not in out
    assert "deep reasoning here" in out
    assert "[reasoning" in out
    thinking_lines = [line for line in out.splitlines() if "[reasoning]" in line]
    assert len(thinking_lines) == 1, (
        f"Expected exactly 1 [reasoning] entry on close, got {len(thinking_lines)}:\n{out}"
    )


def test_stream_parsed_agent_activity_correlates_read_result_without_preview_duplication(
    tmp_path: Path,
) -> None:
    """A Claude result retains its correlated content in its single result entry."""
    pd, buf = _make_display(tmp_path)
    tool_use = json.dumps(
        {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_use",
                "id": "call-1",
                "name": "mcp__ralph__read_file",
                "input": {"path": "src/example.py", "line_start": 17},
            },
        }
    )
    tool_result = json.dumps(
        {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_result",
                "tool_use_id": "call-1",
                "content": '{"content":"def render():\\n    return 1\\n","total_lines":50}',
            },
        }
    )
    stream_parsed_agent_activity(
        [tool_use, tool_result], parser_type="claude", agent_name="claude/sonnet", display=pd
    )
    pd.stop()
    output = buf.getvalue()
    assert "read_file" in output
    assert "def render" in output
    assert "src/example.py" in output


def test_stream_parsed_agent_activity_tool_use_routes_to_structured_path(tmp_path: Path) -> None:
    """_stream_parsed_agent_activity routes tool_use via emit_parsed_event with no duplication."""

    pd, buf = _make_display(tmp_path)

    tool_line = json.dumps(
        {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_use",
                "name": "mcp__ralph__read_file",
                "input": {"path": "ralph-workflow/ralph/x.py"},
            },
        }
    )

    stream_parsed_agent_activity(
        [tool_line],
        parser_type="claude",
        agent_name="claude/sonnet",
        display=pd,
    )

    out = buf.getvalue()
    assert "[output][activity]" not in out
    assert "ralph.read_file" in out
    assert out.count("ralph.read_file") == 1
    # wt-028-display S-3 (DA-001): the public tool_use tag is ``call``,
    # not the retired ``[tool]`` parser-kind identifier.
    assert "[call]" in out
    assert "[tool]" not in out


def test_stream_parsed_agent_activity_session_sink_ignores_nested_tool_payload_session_id(
    tmp_path: Path,
) -> None:
    pd, _buf = _make_display(tmp_path)
    seen: list[str] = []

    lines = [
        json.dumps({"type": "tool_result", "content": {"session_id": "tool-payload"}}),
        json.dumps({"type": "session", "session_id": "transport-session"}),
    ]

    stream_parsed_agent_activity(
        lines,
        parser_type="generic",
        agent_name="claude/sonnet",
        display=pd,
        session_id_sink=seen.append,
    )

    assert seen == ["transport-session"]


def test_stream_parsed_agent_activity_plain_tool_line_routes_to_tool_use(tmp_path: Path) -> None:
    pd, buf = _make_display(tmp_path)

    stream_parsed_agent_activity(
        ["[plain] tool: read_file"],
        parser_type="generic",
        agent_name="nanocoder/minimax",
        display=pd,
    )

    out = buf.getvalue()
    assert "read_file" in out
    assert "[output][activity]" not in out


def test_live_read_result_preserves_correlated_window_line_numbers() -> None:
    """A partial MCP read retains the request window in its live preview gutter."""
    import io
    import re

    from rich.console import Console

    from ralph.display.context import make_display_context
    from ralph.display.parallel_display import ParallelDisplay

    output = io.StringIO()
    display = ParallelDisplay(
        make_display_context(
            console=Console(file=output, force_terminal=True, color_system="truecolor"), env={}
        )
    )
    display.emit_parsed_event(
        "u1",
        ActivityEventKind.TOOL_USE,
        "mcp__ralph__read_file",
        {"input": {"path": "x.py", "line_start": 17, "line_end": 18}},
    )
    display.emit_parsed_event(
        "u1",
        ActivityEventKind.TOOL_RESULT,
        '{"path":"x.py","content":"def render():\\n    return 1\\n","total_lines":50,"returned_lines":2,"truncated":true}',
        {},
    )
    rendered = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output.getvalue())
    assert re.search(r"\b17\s+def render\(\):", rendered)
    assert re.search(r"\b18\s+return 1", rendered)
