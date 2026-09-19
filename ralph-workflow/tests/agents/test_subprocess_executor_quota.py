"""Regression coverage for quota signals in parallel worker output."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from ralph.agents.invoke._quota_exhausted_error import QuotaExhaustedError
from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.pipeline.work_units import WorkUnit
from ralph.process.manager import ProcessManager, ProcessManagerPolicy
from ralph.testing.fake_process import FakeControllableAsyncProcess


async def _run_with_output(output: bytes, *, completes_normally: bool) -> list[str]:
    completion = asyncio.Event()
    if completes_normally:
        completion.set()
    process = FakeControllableAsyncProcess(
        pid=42,
        stdout_data=output,
        completion_event=completion,
    )

    async def fake_factory(
        command: Sequence[str],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        start_new_session: bool,
    ) -> FakeControllableAsyncProcess:
        del command, cwd, env, stdin, stdout, stderr, start_new_session
        return process

    manager = ProcessManager(
        async_process_factory=fake_factory,
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.1,
            kill_followup_timeout_s=0.1,
            log_events=False,
            enable_zombie_reaper=False,
        ),
    )
    output_lines: list[str] = []
    executor = SubprocessAgentExecutor(command=("/opt/agents/worker-cli",), _pm=manager)

    await executor.run(
        WorkUnit(unit_id="quota-worker", description="quota regression"),
        on_output=output_lines.append,
        on_status=lambda _status: None,
    )
    return output_lines


@pytest.mark.asyncio
async def test_worker_quota_output_terminates_and_raises_with_executable_name() -> None:
    """A shared quota signal aborts a still-running parallel worker."""

    with pytest.raises(QuotaExhaustedError, match="worker-cli") as excinfo:
        await _run_with_output(b"rate limit reached\n", completes_normally=False)

    assert "quota or rate limit is exhausted" in str(excinfo.value)


@pytest.mark.asyncio
async def test_worker_authentication_output_remains_regular_progress() -> None:
    """Authentication failures do not use the quota terminal path."""

    output_lines = await _run_with_output(b"authentication failed\n", completes_normally=True)

    assert output_lines == ["authentication failed"]
