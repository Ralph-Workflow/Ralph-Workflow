from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.display.raw_overflow import RawOverflowLog, detect_raw_log_breaks

pytestmark = pytest.mark.timeout_seconds(5)


def _long_wire_frame() -> str:
    return json.dumps(
        {
            "type": "item.completed",
            "item": {"id": "item_18", "type": "mcp_tool_call", "result": "Z" * 600},
        }
    )


def test_sanitizer_truncation_would_corrupt_a_wire_frame() -> None:
    from ralph.display.line_sanitizer import sanitize_display_line

    frame = _long_wire_frame()
    sanitized = sanitize_display_line(frame)

    assert sanitized != frame
    with pytest.raises(json.JSONDecodeError):
        json.loads(sanitized)


def test_verbatim_capture_keeps_the_full_wire_frame(tmp_path: Path) -> None:
    frame = _long_wire_frame()
    log = RawOverflowLog(tmp_path, "codex")
    try:
        log.append(frame)
        log.flush()
        written = log.path.read_text(encoding="utf-8").strip()
    finally:
        log.close()

    assert json.loads(written)["item"]["result"] == "Z" * 600
    assert detect_raw_log_breaks(log.path) == []


def test_the_stream_buffer_admits_a_realistic_agent_frame() -> None:
    from ralph.process.manager import AGENT_STREAM_BUFFER_BYTES

    largest_measured_frame_bytes = 24 * 1024 * 1024

    assert largest_measured_frame_bytes < AGENT_STREAM_BUFFER_BYTES


@pytest.mark.asyncio
async def test_an_oversized_frame_is_dropped_whole(tmp_path: Path) -> None:
    del tmp_path
    from ralph.agents.subprocess_executor import drain_agent_lines

    stream = asyncio.StreamReader(limit=64)
    stream.feed_data(b"x" * 512 + b"\n")
    stream.feed_data(b'{"type":"next"}\n')
    stream.feed_eof()

    lines = [line async for line in drain_agent_lines(stream, "unit-1")]

    assert lines == [b'{"type":"next"}\n']


@pytest.mark.asyncio
async def test_an_oversized_frame_arriving_in_chunks_leaks_no_tail() -> None:
    from ralph.agents.subprocess_executor import drain_agent_lines

    stream = asyncio.StreamReader(limit=64)
    drained: list[bytes] = []

    async def _consume() -> None:
        async for line in drain_agent_lines(stream, "unit-1"):
            drained.append(line)

    task = asyncio.get_running_loop().create_task(_consume())
    for _ in range(4):
        stream.feed_data(b"x" * 64)
        await asyncio.sleep(0)
    stream.feed_data(b'tail-of-oversized-frame"}\n')
    await asyncio.sleep(0)
    stream.feed_data(b'{"type":"next"}\n')
    stream.feed_eof()
    await task

    assert not any(b"tail-of-oversized-frame" in line for line in drained), drained
    assert drained == [b'{"type":"next"}\n']


@pytest.mark.asyncio
async def test_trailing_output_without_a_newline_is_still_captured() -> None:
    from ralph.agents.subprocess_executor import drain_agent_lines

    stream = asyncio.StreamReader(limit=64)
    stream.feed_data(b'{"type":"first"}\n')
    stream.feed_data(b'{"type":"truncated"}')
    stream.feed_eof()

    lines = [line async for line in drain_agent_lines(stream, "unit-1")]

    assert lines == [b'{"type":"first"}\n', b'{"type":"truncated"}']


def test_agent_stdout_decodes_with_replacement() -> None:
    import subprocess
    import sys

    from ralph.process.manager import SpawnOptions, get_process_manager

    emit_bad_byte = (
        "import sys;sys.stdout.buffer.write(b'before\\n\\xff\\nafter\\n');sys.stdout.buffer.flush()"
    )
    manager = get_process_manager()
    handle = manager.spawn(
        [sys.executable, "-c", emit_bad_byte],
        SpawnOptions(stdout=subprocess.PIPE, text=True, label="test:decode-replacement"),
    )
    try:
        stdout = handle.stdout
        assert stdout is not None
        lines = [line.rstrip("\n") for line in stdout]
    finally:
        manager.shutdown_all(grace_period_s=0)

    assert lines[0] == "before"
    assert lines[-1] == "after"
    assert "\ufffd" in lines[1]


@pytest.mark.asyncio
async def test_a_frame_at_the_buffer_limit_is_still_delivered() -> None:
    from ralph.agents.subprocess_executor import drain_agent_lines

    stream = asyncio.StreamReader(limit=64)
    exact = b"y" * 63 + b"\n"
    stream.feed_data(exact)
    stream.feed_eof()

    lines = [line async for line in drain_agent_lines(stream, "unit-1")]

    assert lines == [exact]


@pytest.mark.asyncio
async def test_every_dropped_oversized_frame_leaves_a_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from ralph.agents.subprocess_executor import drain_agent_lines

    buffer_bytes = 128
    monkeypatch.setattr("ralph.agents.subprocess_executor.AGENT_STREAM_BUFFER_BYTES", buffer_bytes)
    oversized = b"X" * (buffer_bytes + 10)
    cases = {
        "middle": ([b"a\n", oversized + b"\n", b"c\n"], [b"a\n", b"c\n"], [False]),
        "at_eof_no_newline": ([b"a\n", oversized], [b"a\n"], [True]),
        "last_with_newline": ([b"a\n", oversized + b"\n"], [b"a\n"], [False]),
        "two_in_a_row": (
            [b"a\n", oversized + b"\n", oversized + b"\n", b"z\n"],
            [b"a\n", b"z\n"],
            [False, False],
        ),
    }

    for label, (chunks, expected_lines, expected_eof_flags) in cases.items():
        stream = asyncio.StreamReader(limit=buffer_bytes)
        for chunk in chunks:
            stream.feed_data(chunk)
        stream.feed_eof()

        markers: list[str] = []
        lines = [
            line
            async for line in drain_agent_lines(stream, "unit-1", on_dropped_frame=markers.append)
        ]

        assert lines == expected_lines, label
        parsed = [json.loads(marker) for marker in markers]
        assert [m["at_eof"] for m in parsed] == expected_eof_flags, label
        assert all(m["type"] == "ralph.capture.dropped_frame" for m in parsed), label


@pytest.mark.asyncio
async def test_the_capture_keeps_bytes_that_are_not_valid_utf8(tmp_path: Path) -> None:
    import sys

    from ralph.display.activity_router import ActivityRouter
    from ralph.display.raw_overflow import close_all_raw_overflow_logs
    from ralph.pipeline.work_units import WorkUnit

    torn = rb'{"bin":"\xff\xfe"}'
    executor = SubprocessAgentExecutor(
        [
            sys.executable,
            "-c",
            'import sys; sys.stdout.buffer.write(b\'{"bin":"\\xff\\xfe"}\\n\')',
        ],
        activity_router=ActivityRouter(),
        raw_overflow_root=tmp_path,
    )

    await executor.run(
        WorkUnit(unit_id="byte-faithful", description="a unit"),
        on_output=lambda _line: None,
        on_status=lambda *_args, **_kwargs: None,
    )
    close_all_raw_overflow_logs()

    written = (tmp_path / ".agent" / "raw" / "byte-faithful.log").read_bytes()

    assert b"\xff\xfe" in written, written
    assert "�".encode() not in written, "the torn bytes were replaced before capture"
    assert written.strip() == torn.replace(rb"\xff\xfe", b"\xff\xfe")
