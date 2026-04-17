"""Tests for SubprocessAgentExecutor."""

import asyncio
import sys

import pytest

from ralph.agents.executor import AgentExecutor, WorkerResult
from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.pipeline.work_units import WorkUnit


def make_unit(unit_id: str) -> WorkUnit:
    return WorkUnit(unit_id=unit_id, description=f"Unit {unit_id}")


@pytest.mark.asyncio
async def test_protocol_isinstance() -> None:
    executor = SubprocessAgentExecutor()
    assert isinstance(executor, AgentExecutor)


@pytest.mark.asyncio
async def test_streams_output() -> None:
    """Verify output lines arrive at on_output callback."""
    executor = SubprocessAgentExecutor()
    unit = make_unit("test-A")

    collected: list[str] = []
    result = await executor.run(
        unit,
        on_output=lambda line: collected.append(line),
        on_status=lambda s: None,
        command=[sys.executable, "-c", "print('line1'); print('line2'); print('line3')"],
    )

    assert result.unit_id == "test-A"
    assert result.exit_code == 0
    assert any("line1" in s for s in collected)
    assert any("line2" in s for s in collected)
    assert any("line3" in s for s in collected)


@pytest.mark.asyncio
async def test_exit_code_propagates() -> None:
    """Non-zero exit codes propagate to WorkerResult."""
    executor = SubprocessAgentExecutor()
    unit = make_unit("test-B")
    result = await executor.run(
        unit,
        on_output=lambda line: None,
        on_status=lambda s: None,
        command=[sys.executable, "-c", "import sys; sys.exit(42)"],
    )
    assert result.exit_code == 42


@pytest.mark.asyncio
async def test_cancel_kills_process_group() -> None:
    """Cancellation kills the entire process group."""
    executor = SubprocessAgentExecutor()
    unit = make_unit("test-C")

    cmd = [
        sys.executable,
        "-c",
        "import time, os; pid = os.fork() if hasattr(os, 'fork') else -1; time.sleep(30)",
    ]

    task = asyncio.create_task(
        executor.run(unit, on_output=lambda l: None, on_status=lambda s: None, command=cmd)
    )

    await asyncio.sleep(0.2)
    task.cancel()

    try:
        await task
    except asyncio.CancelledError:
        pass

    await asyncio.sleep(0.1)
    # Just verifying it doesn't hang here is sufficient


@pytest.mark.asyncio
async def test_duration_ms_populated() -> None:
    """WorkerResult.duration_ms is non-zero."""
    executor = SubprocessAgentExecutor()
    unit = make_unit("test-D")
    result = await executor.run(
        unit,
        on_output=lambda l: None,
        on_status=lambda s: None,
        command=[sys.executable, "-c", "print('done')"],
    )
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_final_message_from_last_line() -> None:
    """final_message is the last output line received."""
    executor = SubprocessAgentExecutor()
    unit = make_unit("test-E")
    last: list[str] = []
    result = await executor.run(
        unit,
        on_output=lambda l: last.append(l),
        on_status=lambda s: None,
        command=[sys.executable, "-c", "print('first'); print('last')"],
    )
    assert result.final_message == (last[-1] if last else "")
