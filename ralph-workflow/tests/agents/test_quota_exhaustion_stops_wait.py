"""Regression coverage for definitive quota signals on live reader streams.

Both public readers must stop as soon as they observe a definitive quota
signal. The process stream returns one quota line and raises a test-local
would-block error if the reader asks for a second blocking read. The AGY PTY
case emits no stdout and appends quota evidence after reader start to its
explicitly passed CLI-log path. The injected ``FakeClock`` keeps either
regression fast.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke._process_reader import ProcessLineReader
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.invoke._quota_exhausted_error import QuotaExhaustedError
from ralph.agents.invoke._types import ProcessReaderCtx
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig

if TYPE_CHECKING:
    import pytest



class _WouldBlockError(Exception):
    """Raised when a reader attempts another blocking source read."""


class _QuotaLineThenWouldBlock:
    def __init__(self, line: str) -> None:
        self._line = line
        self._first_read_complete = False
        self.second_read_attempted = False

    def __iter__(self) -> _QuotaLineThenWouldBlock:
        return self

    def __next__(self) -> str:
        if self._first_read_complete:
            self.second_read_attempted = True
            raise _WouldBlockError("reader requested another blocking stdout read")
        self._first_read_complete = True
        return self._line


class _ProcessHandle:
    stdin = None

    def __init__(self, stdout: _QuotaLineThenWouldBlock) -> None:
        self.pid: int | None = None
        self.stdout = stdout
        self.terminate_calls: list[float] = []

    def poll(self) -> int:
        return 0

    def terminate(self, *, grace_period_s: float = 0.5) -> None:
        self.terminate_calls.append(grace_period_s)


class _PtyHandle:
    def __init__(self, master_fd: int) -> None:
        self.master_fd = master_fd
        self.pid: int | None = None
        self.terminate_calls: list[float] = []

    def poll(self) -> int:
        return 0

    def terminate(self, *, grace_period_s: float = 0.5) -> None:
        self.terminate_calls.append(grace_period_s)

    def descendant_snapshot(self) -> tuple[int, float | None]:
        return (0, None)


class _CompletedThread:
    def join(self, timeout: float | None = None) -> None:
        del timeout


def _policy() -> TimeoutPolicy:
    return TimeoutPolicy(
        idle_timeout_seconds=300.0,
        process_monitor_enabled=False,
    )


def _process_ctx(tmp_path: Path) -> ProcessReaderCtx:
    return ProcessReaderCtx(
        config=AgentConfig(cmd="codex", transport=AgentTransport.CODEX),
        policy=_policy(),
        workspace_path=tmp_path,
    )


def _agy_ctx(tmp_path: Path, cli_log_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        config=AgentConfig(cmd="agy", transport=AgentTransport.AGY),
        policy=_policy(),
        monitor=None,
        execution_strategy=None,
        liveness_probe=None,
        waiting_listener=None,
        workspace_path=tmp_path,
        agy_cli_log_path=cli_log_path,
    )


def _reader_error(reader: ProcessLineReader | PtyLineReader) -> BaseException | None:
    try:
        list(reader.read_lines())
    except BaseException as exc:
        return exc
    return None


def test_process_reader_stops_after_other_agent_quota_line(tmp_path: Path) -> None:
    """A non-interactive rate-limit line aborts before stdout can block again."""
    stdout = _QuotaLineThenWouldBlock("rate limit reached\n")
    reader = ProcessLineReader(
        _ProcessHandle(stdout),
        _process_ctx(tmp_path),
        FakeClock(start=0.0),
    )

    error = _reader_error(reader)

    assert isinstance(error, QuotaExhaustedError), error
    assert stdout.second_read_attempted is False


def test_pty_reader_stops_after_quota_is_appended_to_explicit_cli_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AGY's newly appended CLI-log quota aborts before the PTY can block."""
    cli_log = tmp_path / "cli.log"
    cli_log.write_bytes(b"previous invocation\n")
    master_fd = os.open("/dev/null", os.O_RDONLY)
    reader: PtyLineReader | None = None
    try:
        handle = _PtyHandle(master_fd)
        reader = PtyLineReader(
            handle,
            "agy",
            _agy_ctx(tmp_path, cli_log),
            FakeClock(start=0.0),
            extras=None,
        )

        def read_no_stdout_and_append_quota() -> None:
            cli_log.write_bytes(
                b"previous invocation\nRESOURCE_EXHAUSTED (code 429)\n",
            )
            with reader._lines_lock:
                reader._reader_done[0] = True
            reader._lines_event.set()

        def start_inline(target: object) -> _CompletedThread:
            assert callable(target)
            target()
            return _CompletedThread()

        monkeypatch.setattr(reader, "_read_thread", read_no_stdout_and_append_quota)
        monkeypatch.setattr(reader, "_transcript_thread", lambda: None)
        monkeypatch.setattr(reader, "_sentinel_thread", lambda: None)
        monkeypatch.setattr(reader, "_completion_evidence_thread", lambda: None)
        monkeypatch.setattr(reader, "_start_thread", start_inline)

        error = _reader_error(reader)

        assert isinstance(error, QuotaExhaustedError), error
        assert handle.terminate_calls == [0.5]
    finally:
        if reader is not None:
            with contextlib.suppress(OSError):
                os.close(reader._input_writer_fd)
            with contextlib.suppress(OSError):
                os.close(reader._read_fd)
        os.close(master_fd)
