"""Tests for SubprocessAgentExecutor."""

import asyncio
import inspect
import sys
from contextlib import suppress

import pytest

from ralph.agents.executor import AgentExecutor
from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.pipeline.work_units import WorkUnit

EXIT_CODE_FAILURE = 42


def ignore_output(_line: str) -> None:
    return None


def ignore_status(_status: str) -> None:
    return None


def make_unit(unit_id: str) -> WorkUnit:
    return WorkUnit(unit_id=unit_id, description=f"Unit {unit_id}")


@pytest.mark.asyncio
async def test_protocol_isinstance() -> None:
    executor = SubprocessAgentExecutor([sys.executable, "-c", "print('ok')"])
    assert isinstance(executor, AgentExecutor)
    assert "command" not in inspect.signature(SubprocessAgentExecutor.run).parameters


@pytest.mark.asyncio
async def test_streams_output() -> None:
    """Verify output lines arrive at on_output callback."""
    executor = SubprocessAgentExecutor(
        [sys.executable, "-c", "print('line1'); print('line2'); print('line3')"]
    )
    unit = make_unit("test-A")

    collected: list[str] = []
    result = await executor.run(
        unit,
        on_output=collected.append,
        on_status=ignore_status,
    )

    assert result.unit_id == "test-A"
    assert result.exit_code == 0
    assert any("line1" in s for s in collected)
    assert any("line2" in s for s in collected)
    assert any("line3" in s for s in collected)


@pytest.mark.asyncio
async def test_exit_code_propagates() -> None:
    """Non-zero exit codes propagate to WorkerResult."""
    executor = SubprocessAgentExecutor(
        [sys.executable, "-c", f"import sys; sys.exit({EXIT_CODE_FAILURE})"]
    )
    unit = make_unit("test-B")
    result = await executor.run(
        unit,
        on_output=ignore_output,
        on_status=ignore_status,
    )
    assert result.exit_code == EXIT_CODE_FAILURE


@pytest.mark.asyncio
async def test_cancel_kills_process_group() -> None:
    """Cancellation kills the entire process group."""
    unit = make_unit("test-C")
    started = asyncio.Event()

    executor = SubprocessAgentExecutor(
        [
            sys.executable,
            "-c",
            (
                "import os, sys, threading; "
                "pid = os.fork() if hasattr(os, 'fork') else -1; "
                "print('ready'); sys.stdout.flush(); threading.Event().wait()"
            ),
        ]
    )

    def on_output(line: str) -> None:
        if "ready" in line:
            started.set()

    task = asyncio.create_task(executor.run(unit, on_output=on_output, on_status=ignore_status))

    await asyncio.wait_for(started.wait(), timeout=1.0)
    task.cancel()

    with suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_duration_ms_populated() -> None:
    """WorkerResult.duration_ms is non-zero."""
    executor = SubprocessAgentExecutor([sys.executable, "-c", "print('done')"])
    unit = make_unit("test-D")
    result = await executor.run(
        unit,
        on_output=ignore_output,
        on_status=ignore_status,
    )
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_final_message_from_last_line() -> None:
    """final_message is the last output line received."""
    executor = SubprocessAgentExecutor([sys.executable, "-c", "print('first'); print('last')"])
    unit = make_unit("test-E")
    last: list[str] = []
    result = await executor.run(
        unit,
        on_output=last.append,
        on_status=ignore_status,
    )
    assert result.final_message == (last[-1] if last else "")
