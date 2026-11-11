from __future__ import annotations

from collections.abc import Sequence

import pytest

from ralph.agents.executor import ExecutorError
from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerStatus
from ralph.process import ProcessManager, ProcessManagerPolicy
from ralph.testing.fake_process import FakePsutil


def _failing_factory(
    exc: OSError,
) -> object:

    async def factory(
        command: Sequence[str],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        start_new_session: bool,
    ) -> object:
        del command, cwd, env, stdin, stdout, stderr, start_new_session
        raise exc

    return factory


def _build_pm(exc: OSError) -> ProcessManager:
    return ProcessManager(
        async_process_factory=_failing_factory(exc),
        psutil=FakePsutil(),
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.0,
            kill_followup_timeout_s=0.0,
            log_events=False,
            enable_zombie_reaper=False,
        ),
    )


async def test_missing_binary_message_mentions_binary_and_install_url() -> None:
    err = FileNotFoundError(2, "No such file or directory", "claude")
    pm = _build_pm(err)

    statuses: list[WorkerStatus] = []

    executor = SubprocessAgentExecutor(
        command=("claude", "--help"),
        _pm=pm,
    )
    unit = WorkUnit(unit_id="missing-binary", description="missing-binary-test")

    with pytest.raises(ExecutorError) as excinfo:
        await executor.run(
            unit,
            on_output=lambda _line: None,
            on_status=statuses.append,
        )

    message = str(excinfo.value)
    assert "claude" in message, (
        f"Missing-binary ExecutorError MUST name the binary that was not found; got: {message!r}"
    )
    assert "https://docs.claude.com/claude-code" in message, (
        f"Missing-binary ExecutorError MUST include the install URL from AGENT_INSTALL_URLS; got: {message!r}"
    )
    assert "[agents.*]" in message, (
        f"Missing-binary ExecutorError MUST point at the [agents.*] config section as an alternative fix; got: {message!r}"
    )
    assert "WHY:" in message, (
        f"Missing-binary ExecutorError MUST follow the what/why/fix envelope and include a WHY line; got: {message!r}"
    )
    assert "FIX:" in message, (
        f"Missing-binary ExecutorError MUST follow the what/why/fix envelope and include a FIX line; got: {message!r}"
    )
    assert statuses, "on_status callback must have been invoked at least once"
    assert statuses[-1] is WorkerStatus.FAILED, (
        f"on_status callback final value MUST be FAILED when the binary is missing; got: {statuses}"
    )


async def test_missing_binary_message_for_unknown_binary_points_only_at_config() -> None:
    err = FileNotFoundError(2, "No such file or directory", "my-custom-cli")
    pm = _build_pm(err)

    executor = SubprocessAgentExecutor(
        command=("my-custom-cli", "run"),
        _pm=pm,
    )
    unit = WorkUnit(unit_id="missing-unknown", description="missing-unknown-test")

    with pytest.raises(ExecutorError) as excinfo:
        await executor.run(
            unit,
            on_output=lambda _line: None,
            on_status=lambda _status: None,
        )

    message = str(excinfo.value)
    assert "my-custom-cli" in message, (
        f"Missing-binary ExecutorError MUST name the unknown binary; got: {message!r}"
    )
    assert "[agents.*]" in message, (
        f"Missing-binary ExecutorError MUST point at [agents.*] as the alternative fix; got: {message!r}"
    )
    assert "WHY:" in message and "FIX:" in message, (
        f"Missing-binary ExecutorError MUST follow the what/why/fix envelope; got: {message!r}"
    )
