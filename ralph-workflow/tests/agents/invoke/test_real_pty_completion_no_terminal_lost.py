"""Real-PTY regression test: child terminated after /exit preserves returncode.

wt-16 / U3: verifies that an interactive child ignoring /exit and terminated
via PtyLineReader._request_interactive_exit preserves its returncode and
passes check_process_result without raising or losing terminal status.
"""

from __future__ import annotations

import contextlib
import sys
import threading
from typing import TYPE_CHECKING

import pytest

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.idle_watchdog.timeout_policy import TimeoutPolicy
from ralph.agents.invoke._bounded_lines_queue import BoundedLinesQueue
from ralph.agents.invoke._completion import CompletionCheckOptions, check_process_result
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.config.enums import AgentTransport
from ralph.process.manager import ProcessStatus
from ralph.process.manager._process_manager import ProcessManager
from ralph.process.manager._process_manager_policy import ProcessManagerPolicy
from ralph.process.manager._pty_spawn_options import PtySpawnOptions

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.smoke,
]  # wt-03-fix-bug: subprocess_e2e removed; runs in E2E shard only


def _terminal_signals(
    workspace: Path,
    raw_output: list[str] | None = None,
    *,
    required_artifact: object = None,
    run_id: str | None = None,
    sentinel_secret: str | None = None,
    receipt_secret: str | None = None,
) -> CompletionSignals:
    del workspace, raw_output, required_artifact, run_id, sentinel_secret, receipt_secret
    return CompletionSignals(
        explicit_complete=True,
        required_artifact_present=False,
        artifact_types=(),
        completion_sentinel_present=True,
    )


@pytest.mark.skipif(sys.platform == "win32", reason="PTY is POSIX-only")
@pytest.mark.timeout_seconds(3)
def test_request_interactive_exit_does_not_lose_returncode(tmp_path: Path) -> None:
    pm = ProcessManager(
        ProcessManagerPolicy(
            default_grace_period_s=0.02,
            kill_followup_timeout_s=0.02,
            log_events=False,
            enable_zombie_reaper=False,
        )
    )
    handle = pm.spawn_pty(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        PtySpawnOptions(label="invoke:agy"),
    )

    try:
        policy = TimeoutPolicy(idle_timeout_seconds=5.0, parent_exit_grace_seconds=0.02)
        reader = object.__new__(PtyLineReader)
        reader._handle = handle
        reader._agent_name = "agy"
        reader._completion_exit_sent = False
        reader._lines_lock = threading.Lock()
        reader._lines_queue = BoundedLinesQueue(maxlen=256)
        reader._lines_event = threading.Event()
        reader._input_writer_fd = handle.master_fd
        reader._input_writer_lock = threading.Lock()
        reader._policy = policy
        reader._raw_overflow = None

        reader._request_interactive_exit()

        assert isinstance(handle.returncode, int)
        assert handle.record.returncode == handle.returncode
        assert handle.record.status in (
            ProcessStatus.EXITED,
            ProcessStatus.KILLED,
            ProcessStatus.FAILED,
        )

        opts = CompletionCheckOptions(
            execution_strategy=strategy_for_transport(AgentTransport.AGY),
            workspace_path=tmp_path,
            evaluate_completion_fn=_terminal_signals,
        )

        check_process_result(
            handle,
            "agy",
            parsed_output=["result SUCCESS"],
            check_options=opts,
        )
        assert handle.record.status in (
            ProcessStatus.EXITED,
            ProcessStatus.KILLED,
            ProcessStatus.FAILED,
        )
    finally:
        with contextlib.suppress(Exception):
            handle.terminate(grace_period_s=0.01)
        with contextlib.suppress(Exception):
            handle.close()
