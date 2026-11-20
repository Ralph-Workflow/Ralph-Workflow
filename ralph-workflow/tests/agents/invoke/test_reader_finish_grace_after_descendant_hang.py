from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import ProcessLineReader, ProcessReaderCtx
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig

if TYPE_CHECKING:
    from pathlib import Path

_QUOTA_WORDS = "Your account has credits exhausted for this period."
_QUOTA_WORD_TOOL_RESULT_LINE = json.dumps(
    {"type": "item.completed", "item": {"type": "tool_result", "content": _QUOTA_WORDS}}
)
_READER_STREAM_CLOSE_SECONDS = 0.05
_DESCENDANT_WAIT_POLL_SECONDS = 0.5
_DESCENDANT_WAIT_TIMEOUT_SECONDS = 0.0


class _SlowClosingStdout:
    def __init__(self, line: str) -> None:
        self._line = line
        self._served = False

    def __iter__(self) -> _SlowClosingStdout:
        return self

    def __next__(self) -> str:
        if self._served:
            threading.Event().wait(timeout=_READER_STREAM_CLOSE_SECONDS)
            raise StopIteration
        self._served = True
        return self._line + "\n"


class _ExitedProcessHandle:
    stdin = None
    pid: int | None = None
    returncode: int | None = 0

    def __init__(self, stdout: object) -> None:
        self.stdout = stdout
        self.terminate_calls: list[float] = []

    def poll(self) -> int:
        return 0

    def terminate(self, *, grace_period_s: float = 0.5, **_kwargs: object) -> None:
        self.terminate_calls.append(grace_period_s)


def _reader(tmp_path: Path, handle: _ExitedProcessHandle) -> ProcessLineReader:
    policy = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        process_monitor_enabled=False,
        descendant_wait_timeout_seconds=_DESCENDANT_WAIT_TIMEOUT_SECONDS,
        descendant_wait_poll_seconds=_DESCENDANT_WAIT_POLL_SECONDS,
    )
    return ProcessLineReader(
        handle,
        ProcessReaderCtx(
            config=AgentConfig(cmd="codex", transport=AgentTransport.CODEX),
            policy=policy,
            workspace_path=tmp_path,
        ),
        FakeClock(start=0.0),
    )


def test_descendant_hang_verdict_keeps_the_session_when_the_reader_is_still_reading(
    tmp_path: Path,
) -> None:
    handle = _ExitedProcessHandle(_SlowClosingStdout(_QUOTA_WORD_TOOL_RESULT_LINE))
    reader = _reader(tmp_path, handle)

    emitted = list(reader.read_lines())

    assert emitted == [_QUOTA_WORD_TOOL_RESULT_LINE + "\n"]
    assert handle.terminate_calls == []
