"""Tests for ParallelDisplay activity_router content routing to plain renderer."""

from __future__ import annotations

import json
from io import StringIO
from typing import TYPE_CHECKING

from rich.console import Console

from ralph.display.activity_model import ActivityProvider
from ralph.display.parallel_display import ParallelDisplay

if TYPE_CHECKING:
    from pathlib import Path

_LONG_TEXT_LEN = 5000


def _make_display(tmp_path: Path, width: int = 2000) -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=width)
    pd = ParallelDisplay(console, {"CI": "1"}, workspace_root=tmp_path)
    return pd, buf


def test_push_text_line_emits_content_tag(tmp_path: Path) -> None:
    pd, buf = _make_display(tmp_path)
    pd.activity_router.push_raw_line(
        "u",
        '{"type":"content_block_delta","delta":{"type":"text_delta","text":"hello world"}}',
        provider=ActivityProvider.CLAUDE,
    )
    out = buf.getvalue()
    # Streaming blocks: first text event opens [content-start]
    assert "[content" in out
    assert "[u]" in out
    assert "hello world" in out


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
    out = buf.getvalue()
    # Streaming blocks: first thinking event opens [thinking-start]
    assert "[thinking" in out
    assert "[u]" in out
    assert "deep thought" in out


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

    out = buf.getvalue()
    # Streaming blocks use [content-start]/[content-continue]/[content-end] tags
    assert "[content" in out
    # Content should be condensed (not all characters present)
    assert len(out) < _LONG_TEXT_LEN
    assert "…" in out or "truncated" in out or "raw unavailable" in out


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
    import asyncio  # noqa: PLC0415

    from ralph.agents.subprocess_executor import SubprocessAgentExecutor  # noqa: PLC0415
    from ralph.display.activity_router import ActivityRouter  # noqa: PLC0415
    from ralph.pipeline.work_units import WorkUnit  # noqa: PLC0415

    received: list[str] = []

    router = ActivityRouter(on_event=lambda uid, kind, content, ref: received.append(content or ""))

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

    out = buf.getvalue()
    assert "[content" in out
    assert ".agent/raw/u.log" in out
