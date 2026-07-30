"""Friendly error when the agent CLI binary is missing on PATH.

Before the fix, ``SubprocessAgentExecutor.run`` raised a bare
``ExecutorError("Failed to start subprocess: [Errno 2] No such file or directory: 'claude'")``
which gave the operator no hint on which CLI was missing, no install
URL, and no pointer back to the config section to switch agents.

After the fix, a ``FileNotFoundError`` raised by
``ProcessManager.spawn_async`` (because the underlying
``asyncio.create_subprocess_exec`` cannot find the binary) is wrapped
in a structured ``ExecutorError`` whose message follows the project's
what/why/fix envelope and points the operator at the install URL
sourced from ``AGENT_INSTALL_URLS`` (via ``install_url_for``).

This test pins the post-fix behavior using a fake async process factory
that raises ``FileNotFoundError`` (the same error the asyncio event
loop emits when ``execvp`` cannot resolve the binary). No real
subprocess, no real network, no real sleep -- the test runs well under
the per-test 1 s budget.
"""

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
    """Build a fake async process factory that always raises ``exc``.

    The return is annotated ``object`` because the factory never actually
    returns -- it raises -- and the function only needs to satisfy
    ``ProcessManager``'s async-process-factory Protocol (which itself is
    typed ``Any`` outside ``TYPE_CHECKING``).
    """

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
    """``FileNotFoundError`` from spawn_async must produce a what/why/fix envelope.

    Drives ``SubprocessAgentExecutor.run`` against a fake process factory
    that raises ``FileNotFoundError`` with the canonical ``[Errno 2] No
    such file or directory: '<bin>'`` message. The expected post-fix
    behavior:

      * The executor raises ``ExecutorError`` (NOT a bare OSError).
      * The message names the missing binary (``claude``).
      * The message includes the install URL from
        ``AGENT_INSTALL_URLS['claude']`` so the operator can fix the
        problem with one click.
      * The message points the operator at the ``[agents.*]`` config
        section as an alternative fix (switch the active agent).
    """
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
    # The bare "[Errno 2] No such file or directory" is no longer the
    # whole story -- the structured envelope replaces it.
    assert "WHY:" in message, (
        f"Missing-binary ExecutorError MUST follow the what/why/fix envelope and include a WHY line; got: {message!r}"
    )
    assert "FIX:" in message, (
        f"Missing-binary ExecutorError MUST follow the what/why/fix envelope and include a FIX line; got: {message!r}"
    )
    # on_status callback must report a terminal FAILED status so the
    # coordinator can classify the run correctly.
    assert statuses, "on_status callback must have been invoked at least once"
    assert statuses[-1] is WorkerStatus.FAILED, (
        f"on_status callback final value MUST be FAILED when the binary is missing; got: {statuses}"
    )


async def test_missing_binary_message_for_unknown_binary_points_only_at_config() -> None:
    """An unknown binary (no AGENT_INSTALL_URLS entry) still gets the envelope.

    The fallback when ``install_url_for`` returns ``None`` is to point
    the operator at the config section without an install URL. The
    message MUST NOT contain a bare OSError string; it MUST still
    include the what/why/fix envelope.
    """
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
