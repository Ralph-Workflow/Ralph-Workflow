"""The subprocess executor's raw capture must be byte-faithful.

``sanitize_display_line`` exists to make a line safe to PRINT: it strips
terminal control sequences and truncates at 200 characters with a ``…``
suffix. That output was being written into ``.agent/raw/<id>.log``, the
file Ralph reads back as the agent's verbatim transcript and grades for
corruption.

Any wire frame longer than 200 characters therefore landed in the
capture as a severed JSON object -- ``{"type": "item.completed", "item":
{"result": "xxx…`` -- which is exactly the ``NON_JSONL`` shape
``detect_raw_log_breaks`` reports as ``raw transcript corrupted``. A
Codex or Claude frame carrying a tool result is routinely far longer
than 200 characters, so the capture was being truncated into garbage by
the very layer that claims to preserve it.

The display still gets the sanitized line; only the capture gets the
original bytes.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ralph.display.raw_overflow import RawOverflowLog, detect_raw_log_breaks

pytestmark = pytest.mark.timeout_seconds(5)


def _long_wire_frame() -> str:
    """A realistic JSONL frame comfortably past the 200-char display cap."""
    return json.dumps(
        {
            "type": "item.completed",
            "item": {"id": "item_18", "type": "mcp_tool_call", "result": "Z" * 600},
        }
    )


def test_sanitizer_truncation_would_corrupt_a_wire_frame() -> None:
    """Pin the hazard this guards, so the rationale cannot rot."""
    from ralph.display.line_sanitizer import sanitize_display_line

    frame = _long_wire_frame()
    sanitized = sanitize_display_line(frame)

    assert sanitized != frame
    with pytest.raises(json.JSONDecodeError):
        json.loads(sanitized)


def test_verbatim_capture_keeps_the_full_wire_frame(tmp_path: Path) -> None:
    """A frame written to the capture must parse back as JSON."""
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
    """The default 64 KiB asyncio buffer is far below real frame sizes.

    ``StreamReader.readline`` raises ``ValueError`` past its buffer and
    then CLEARS the buffer, so an oversized frame takes everything queued
    behind it and kills the reading loop. Measured captures hold single
    lines of 503 KB, 693 KB and 24 MB, because one JSON frame carries a
    whole tool result.
    """
    from ralph.process.manager import AGENT_STREAM_BUFFER_BYTES

    largest_measured_frame_bytes = 24 * 1024 * 1024

    assert largest_measured_frame_bytes < AGENT_STREAM_BUFFER_BYTES


@pytest.mark.asyncio
async def test_an_oversized_frame_is_dropped_whole(tmp_path: Path) -> None:
    """A frame past the buffer must not take the rest of the unit with it."""
    del tmp_path
    from ralph.agents.subprocess_executor import drain_agent_lines

    stream = asyncio.StreamReader(limit=64)
    stream.feed_data(b"x" * 512 + b"\n")
    stream.feed_data(b'{"type":"next"}\n')
    stream.feed_eof()

    lines = [line async for line in drain_agent_lines(stream, "unit-1")]

    # Exact: the oversized frame is dropped WHOLE and the following one
    # survives. A partial tail here would land in the verbatim capture
    # and be graded as damage the agent did.
    assert lines == [b'{"type":"next"}\n']


@pytest.mark.asyncio
async def test_an_oversized_frame_arriving_in_chunks_leaks_no_tail() -> None:
    """The streaming order is where a partial tail could leak.

    With the whole line buffered, the overflowing bytes are consumed in
    one go. Arriving in chunks, the reader is still mid-frame after the
    first overflow, and the remainder must be consumed rather than
    yielded.
    """
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
    """An agent that exits mid-line must not lose its last bytes."""
    from ralph.agents.subprocess_executor import drain_agent_lines

    stream = asyncio.StreamReader(limit=64)
    stream.feed_data(b'{"type":"first"}\n')
    stream.feed_data(b'{"type":"truncated"}')
    stream.feed_eof()

    lines = [line async for line in drain_agent_lines(stream, "unit-1")]

    assert lines == [b'{"type":"first"}\n', b'{"type":"truncated"}']


def test_agent_stdout_decodes_with_replacement() -> None:
    """One undecodable byte must not end a capture.

    Agent stdout is a byte stream Ralph Workflow does not control.
    Under strict decoding the reader thread raised mid-chunk, losing the
    bad line, everything buffered with it, and the rest of the turn --
    silently, because a short-but-parseable file reports no corruption.

    This spawns a real process that emits an invalid UTF-8 byte between
    two good lines and reads it back through the production factory.
    Asserting ``SpawnOptions().errors == "replace"`` instead proved only
    that a dataclass default equals its own literal: the factory could
    stop forwarding the option entirely and that assertion still passed.
    """
    import subprocess
    import sys

    from ralph.process.manager import SpawnOptions, get_process_manager

    emit_bad_byte = (
        "import sys;"
        "sys.stdout.buffer.write(b'before\\n\\xff\\nafter\\n');"
        "sys.stdout.buffer.flush()"
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

    # The undecodable byte survives as U+FFFD, and -- the point -- the
    # lines on either side of it are both still delivered.
    assert lines[0] == "before"
    assert lines[-1] == "after"
    assert "\ufffd" in lines[1]


@pytest.mark.asyncio
async def test_a_frame_at_the_buffer_limit_is_still_delivered() -> None:
    """The drop path must not claim a frame that actually fits."""
    from ralph.agents.subprocess_executor import drain_agent_lines

    stream = asyncio.StreamReader(limit=64)
    exact = b"y" * 63 + b"\n"
    stream.feed_data(exact)
    stream.feed_eof()

    lines = [line async for line in drain_agent_lines(stream, "unit-1")]

    assert lines == [exact]
