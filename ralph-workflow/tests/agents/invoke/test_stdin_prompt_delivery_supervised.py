"""A stdin-delivered prompt must not run outside the reader and its watchdog.

Codex and OpenCode take their prompt on stdin. It was written with one blocking
``write()`` on the calling thread BEFORE the line reader and idle watchdog
existed, so:

* a child that never reads stdin, given a prompt larger than the pipe buffer
  (64 KiB), blocked Ralph forever -- ``idle_timeout_seconds`` and
  ``max_session_seconds`` never got a chance to apply;
* a child that exited early (bad model name, missing credentials) made the
  write raise ``BrokenPipeError``, which escaped instead of the child's own
  exit code and stderr.

These tests run real ``/bin/sh`` children (never a coding agent), so they carry
the ``subprocess_e2e`` marker.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import AgentInvocationError, AgentRunCtx, run_subprocess_and_read_lines
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(15)]

#: Far past the 64 KiB pipe buffer, so an unread write cannot complete.
_PROMPT = "x" * 300_000
_EXIT_CODE = 3
#: How long the caller waits for the run to finish before declaring a hang;
#: comfortably past the session ceiling below.
_HANG_DEADLINE_SECONDS = 8.0


def _ctx(workspace: Path, policy: TimeoutPolicy) -> AgentRunCtx:
    return AgentRunCtx(
        config=AgentConfig(cmd="/bin/sh", transport=AgentTransport.CODEX),
        show_progress=False,
        extra_env=None,
        workspace_path=workspace,
        policy=policy,
    )


def _drive(cmd: list[str], ctx: AgentRunCtx) -> tuple[bool, BaseException | None]:
    """Run the reader on a worker thread; return (finished, raised)."""
    outcome: list[BaseException | None] = []

    def _consume() -> None:
        try:
            for _line in run_subprocess_and_read_lines(cmd, ctx):
                pass
        except BaseException as exc:
            outcome.append(exc)
        else:
            outcome.append(None)

    worker = threading.Thread(target=_consume, daemon=True)
    worker.start()
    worker.join(_HANG_DEADLINE_SECONDS)
    return (not worker.is_alive(), outcome[0] if outcome else None)


def test_child_that_exits_without_reading_stdin_reports_its_own_failure(tmp_path: Path) -> None:
    script = f"echo 'error: model gpt-x not found' >&2; exit {_EXIT_CODE}"
    policy = TimeoutPolicy(idle_timeout_seconds=5.0, max_session_seconds=10.0)

    finished, raised = _drive(["/bin/sh", "-c", script, _PROMPT], _ctx(tmp_path, policy))

    assert finished
    assert not isinstance(raised, BrokenPipeError), f"prompt write escaped: {raised!r}"
    assert isinstance(raised, AgentInvocationError), f"got {raised!r}"
    assert raised.returncode == _EXIT_CODE
    assert "model gpt-x not found" in str(raised)


def test_child_that_never_reads_stdin_is_still_bounded_by_the_watchdog(tmp_path: Path) -> None:
    policy = TimeoutPolicy(idle_timeout_seconds=1.0, max_session_seconds=2.0)

    finished, raised = _drive(
        ["/bin/sh", "-c", "exec /bin/sleep 600", _PROMPT], _ctx(tmp_path, policy)
    )

    assert finished, "a child that never reads its prompt hung the caller past the watchdog"
    assert isinstance(raised, AgentInvocationError), f"got {raised!r}"
