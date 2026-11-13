from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import pty
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from ralph.agents._broker_secret import _parent_broker_secret
from ralph.agents._session_markers import TURN_BOUNDARY_MARKER
from ralph.agents.completion_signals import evaluate_completion
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import AgentRunCtx, PtyLineReader
from ralph.agents.invoke._pty_extras import PtyExtras
from ralph.agents.invoke.attempt_supervision import (
    AttemptSupervisor,
    InteractiveCompletionObserved,
)
from ralph.agents.invoke.attempt_supervision._issuer_mapping import attempt_issuer_for
from ralph.agents.timeout_clock import SystemClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.env import MCP_RUN_ID_ENV
from ralph.mcp.tools.coordination import COMPLETION_SENTINEL_RELPATHFMT
from ralph.recovery.attempt_facts import TerminationIssuer

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.process.manager import ManagedPtyProcess

pytestmark = pytest.mark.subprocess_e2e

_STUB_CLI = r'''
import json
import pathlib
import sys
import time

sentinel_path = pathlib.Path(sys.argv[1])

print(
    json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_01",
                        "name": "Agent",
                        "input": {
                            "description": "map the repository layout",
                            "subagent_type": "explorer",
                        },
                    }
                ],
            },
        }
    ),
    flush=True,
)
print("Waiting on discovery agents.", flush=True)
print(
    "Discovery agents are still running; I'll continue once they report.",
    flush=True,
)

# The Stop hook sentinel fires while the background discovery subagents are
# still outstanding and no artifact exists yet.
time.sleep(0.6)
sentinel_path.touch()

# The session stays alive until the harness asks it to exit.
sys.stdin.readline()
'''


def _spawn_stub(tmp_path: Path) -> tuple[int, subprocess.Popen[bytes], Path]:
    sentinel_path = tmp_path / "stop-hook.done"
    script_path = tmp_path / "stub_cli.py"
    script_path.write_text(_STUB_CLI, encoding="utf-8")
    master_fd, slave_fd = pty.openpty()
    child = subprocess.Popen(
        [sys.executable, str(script_path), str(sentinel_path)],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave_fd)
    return master_fd, child, sentinel_path


class _StubCliHandle:
    def __init__(self, master_fd: int, child: subprocess.Popen[bytes]) -> None:
        self.master_fd = master_fd
        self._child = child
        self.terminate_issuers: list[object] = []
        self.terminal_reasons: list[str] = []

    @property
    def pid(self) -> int:
        return self._child.pid

    def poll(self) -> int | None:
        return self._child.poll()

    def wait(self, timeout: float | None = None) -> int | None:
        with contextlib.suppress(subprocess.TimeoutExpired):
            return self._child.wait(timeout=timeout)
        return None

    def terminate(self, grace_period_s: float = 0.5, **kwargs: object) -> None:
        del grace_period_s
        self.terminate_issuers.append(kwargs.get("issuer"))
        with contextlib.suppress(OSError, ProcessLookupError):
            os.killpg(self._child.pid, signal.SIGKILL)

    def record_terminal_reason(self, reason: str) -> None:
        self.terminal_reasons.append(reason)


_RUN_ID = "run-ac17-pending-subagents"


def _write_completion_sentinel(workspace: Path) -> None:
    sentinel_path = workspace / COMPLETION_SENTINEL_RELPATHFMT.format(run_id=_RUN_ID)
    sentinel_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, str] = {"note": "stop hook fired while subagents were pending"}
    secret = _parent_broker_secret()
    if secret is not None:
        payload = {
            "hmac": hmac.new(
                secret.encode(), _RUN_ID.encode(), hashlib.sha256
            ).hexdigest()
        }
    sentinel_path.write_text(json.dumps(payload), encoding="utf-8")


def _reader(
    tmp_path: Path,
    handle: _StubCliHandle,
    supervisor: AttemptSupervisor,
    sentinel_path: Path,
) -> PtyLineReader:
    ctx = AgentRunCtx(
        config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
        show_progress=False,
        extra_env={str(MCP_RUN_ID_ENV): _RUN_ID},
        workspace_path=tmp_path,
        policy=TimeoutPolicy(
            idle_timeout_seconds=300.0,
            process_monitor_enabled=False,
            parent_exit_grace_seconds=1.0,
        ),
        evaluate_completion_fn=evaluate_completion,
    )
    extras = PtyExtras(
        expected_session_id="sess-pending-subagents",
        stop_sentinel_path=sentinel_path,
        input_prompt="plan the discovery",
    )
    return PtyLineReader(
        cast("ManagedPtyProcess", handle),
        "claude",
        ctx,
        SystemClock(),
        extras=extras,
        supervisor=supervisor,
    )


@pytest.mark.timeout_seconds(10)
def test_turn_end_while_subagents_are_pending_is_unattributed_not_a_fault(
    tmp_path: Path,
) -> None:
    _write_completion_sentinel(tmp_path)
    master_fd, child, sentinel_path = _spawn_stub(tmp_path)
    handle = _StubCliHandle(master_fd, child)
    supervisor = AttemptSupervisor(handle)
    reader = _reader(tmp_path, handle, supervisor, sentinel_path)
    lines: list[str] = []
    try:
        iterator = reader.read_lines()
        try:
            deadline = time.monotonic() + 8.0
            while True:
                if time.monotonic() > deadline:
                    pytest.fail("the reader did not finish the turn end within 8 s")
                try:
                    lines.append(next(iterator))
                except StopIteration:
                    break
        finally:
            close_fn = getattr(iterator, "close", None)
            if callable(close_fn):
                close_fn()

        assert "Waiting on discovery agents.\n" in lines
        assert TURN_BOUNDARY_MARKER + "\n" in lines

        termination = supervisor.recorder.termination()
        assert termination.issuer is TerminationIssuer.UNKNOWN, (
            "a turn end with pending subagents must never be attributed to the"
            f" agent as a fault, got issuer={termination.issuer!r}"
        )
        assert termination.observed_returncode == 0
        assert handle.terminal_reasons == ["interactive_completion", "interactive_completion"]
        assert handle.terminate_issuers == [], (
            "the interactive turn end must not terminate the session as a fault"
        )
        assert child.wait(timeout=5) == 0, (
            "the stub CLI was killed instead of exiting on the interactive exit"
        )
    finally:
        with contextlib.suppress(Exception):
            os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=5)
        os.close(master_fd)


def test_interactive_completion_maps_to_no_attempt_issuer() -> None:
    assert attempt_issuer_for(InteractiveCompletionObserved(grace_s=0.1)) is None
