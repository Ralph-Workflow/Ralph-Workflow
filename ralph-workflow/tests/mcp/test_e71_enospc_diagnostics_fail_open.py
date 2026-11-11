from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.server import lifecycle

if TYPE_CHECKING:
    from ralph.process.manager import SpawnOptions

pytestmark = pytest.mark.timeout_seconds(10)


class _FakeManagedProcess:
    def __init__(self, pid: int = 12345) -> None:
        self._pid = pid
        self.passed_stdout: int | None = None
        self.passed_stderr: int | None = None

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        return None

    def terminate(self, grace_period_s: float = 5.0) -> None:
        del grace_period_s

    def kill(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0


def _recording_spawn(
    recorded_options: list[SpawnOptions],
) -> SpawnOptions:

    def _spawn(
        command: list[str],
        options: SpawnOptions,
    ) -> _FakeManagedProcess:
        del command
        recorded_options.append(options)
        proc = _FakeManagedProcess()
        proc.passed_stdout = options.stdout
        proc.passed_stderr = options.stderr
        return proc

    return _spawn


def _patch_os_open_to_fail(
    monkeypatch: pytest.MonkeyPatch,
    *,
    target_path: Path,
    errno_value: int,
) -> None:

    real_open = os.open

    def _maybe_failing_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *args: object,
        **kwargs: object,
    ) -> int:
        del args, kwargs
        try:
            p = os.fspath(path)
        except TypeError:
            p = str(path)
        if os.path.normpath(p) == os.path.normpath(str(target_path)):
            err = OSError(errno_value, os.strerror(errno_value), p)
            raise err from None
        return real_open(path, flags, mode)

    monkeypatch.setattr(lifecycle.os, "open", _maybe_failing_open)


def test_spawn_process_succeeds_when_diagnostic_open_raises_enospc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    log_path = lifecycle.mcp_server_log_path(cwd)
    assert log_path.parent.exists() is False or log_path.parent.is_dir()
    _patch_os_open_to_fail(monkeypatch, target_path=log_path, errno_value=errno.ENOSPC)
    recorded: list[SpawnOptions] = []
    spawn_fn = _recording_spawn(recorded)

    proc = lifecycle._spawn_process(
        command=["python", "-c", "pass"],
        cwd=cwd,
        env={},
        phase="test",
        spawn=spawn_fn,
        pass_fds=(),
    )

    assert isinstance(proc, _FakeManagedProcess)
    assert len(recorded) == 1
    options = recorded[0]
    assert options.stdout is not None and options.stdout >= 0
    assert options.stderr == options.stdout


def test_spawn_process_succeeds_when_diagnostic_open_raises_erofs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cwd = tmp_path / "workspace-erofs"
    cwd.mkdir()
    log_path = lifecycle.mcp_server_log_path(cwd)
    _patch_os_open_to_fail(monkeypatch, target_path=log_path, errno_value=errno.EROFS)
    recorded: list[SpawnOptions] = []
    spawn_fn = _recording_spawn(recorded)

    proc = lifecycle._spawn_process(
        command=["python", "-c", "pass"],
        cwd=cwd,
        env={},
        phase="test",
        spawn=spawn_fn,
        pass_fds=(),
    )

    assert isinstance(proc, _FakeManagedProcess)
    assert len(recorded) == 1
    options = recorded[0]
    assert options.stdout is not None and options.stdout >= 0
    assert options.stderr == options.stdout


def test_spawn_process_logs_warning_on_enospc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cwd = tmp_path / "workspace-warn"
    cwd.mkdir()
    log_path = lifecycle.mcp_server_log_path(cwd)
    _patch_os_open_to_fail(monkeypatch, target_path=log_path, errno_value=errno.ENOSPC)

    from loguru import logger

    captured: list[str] = []
    sink_id = logger.add(lambda message: captured.append(str(message)), level="WARNING")
    try:
        lifecycle._spawn_process(
            command=["python", "-c", "pass"],
            cwd=cwd,
            env={},
            phase="test",
            spawn=_recording_spawn([]),
            pass_fds=(),
        )
    finally:
        logger.remove(sink_id)

    assert any(
        "diagnostic log setup failed" in record and str(errno.ENOSPC) in record
        for record in captured
    ), captured


def test_spawn_process_propagates_non_environmental_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cwd = tmp_path / "workspace-eio"
    cwd.mkdir()
    log_path = lifecycle.mcp_server_log_path(cwd)
    _patch_os_open_to_fail(monkeypatch, target_path=log_path, errno_value=errno.EIO)

    with pytest.raises(OSError) as excinfo:
        lifecycle._spawn_process(
            command=["python", "-c", "pass"],
            cwd=cwd,
            env={},
            phase="test",
            spawn=_recording_spawn([]),
            pass_fds=(),
        )

    assert excinfo.value.errno == errno.EIO


def test_spawn_process_stage_stays_unknown_after_enospc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cwd = tmp_path / "workspace-stage"
    cwd.mkdir()
    log_path = lifecycle.mcp_server_log_path(cwd)
    _patch_os_open_to_fail(monkeypatch, target_path=log_path, errno_value=errno.ENOSPC)

    lifecycle._spawn_process(
        command=["python", "-c", "pass"],
        cwd=cwd,
        env={},
        phase="test",
        spawn=_recording_spawn([]),
        pass_fds=(),
    )

    assert not log_path.exists()
    assert lifecycle._read_start_stage(cwd, spawn_id="test") is None


def test_record_contention_deferral_does_not_leak_untyped_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy

    bridge = lifecycle.RestartAwareMcpBridge(
        inner=lifecycle.StandaloneMcpProcess(
            endpoint="http://127.0.0.1:9100/mcp",
            process=_FakeManagedProcess(pid=7777),
            session_file=tmp_path / "session.json",
        ),
        restart_fn=lambda **_kwargs: lifecycle.StandaloneMcpProcess(
            endpoint="http://127.0.0.1:9100/mcp",
            process=_FakeManagedProcess(pid=8888),
            session_file=tmp_path / "session-other.json",
        ),
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="run-ledger",
    )
    broken_sink = tmp_path / "missing" / "subdir" / "events.jsonl"
    bridge._durable_sink_path = broken_sink

    bridge._record_contention_deferral(
        probe_duration_s=0.5,
        deferral_count=1,
        stage="probe_deferred_contention",
    )


def test_record_contention_deferral_typed_failure_path_still_raises_mcp_server_error(
    tmp_path: Path,
) -> None:
    bridge = lifecycle.RestartAwareMcpBridge(
        inner=lifecycle.StandaloneMcpProcess(
            endpoint="http://127.0.0.1:9100/mcp",
            process=_FakeManagedProcess(pid=9999),
            session_file=tmp_path / "session-budget.json",
        ),
        restart_fn=lambda **_kwargs: lifecycle.StandaloneMcpProcess(
            endpoint="http://127.0.0.1:9100/mcp",
            process=_FakeManagedProcess(pid=10000),
            session_file=tmp_path / "session-budget-other.json",
        ),
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=1, cooldown_seconds=0.0),
        run_id="run-budget",
    )
    bridge._restart_count = bridge._restart_policy.max_restarts

    with pytest.raises(lifecycle.McpServerError):
        bridge._perform_restart(process_exited=False, probe_duration_s=0.25)
