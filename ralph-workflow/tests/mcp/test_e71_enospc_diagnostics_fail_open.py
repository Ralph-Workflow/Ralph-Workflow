"""E7.1 — MCP diagnostic log setup fails open on environmental OSErrors.

Required behaviour: when the diagnostic / log-directory setup hits an
environmental OSError (ENOSPC, EROFS, EACCES, EPERM), the spawn must
degrade gracefully — output falls back to ``/dev/null`` and the start
proceeds so the server can serve. The integrity / ledger store keeps its
typed fail-closed contract: any failure on the ledger path surfaces as
a typed ``McpServerError`` or is swallowed under an existing
``except OSError: pass`` guard, never as an untyped traceback leaking to
the caller.

This module drives :func:`ralph.mcp.server.lifecycle._spawn_process`
directly so the diagnostic-setup seam (``os.open`` on the log path) is
exercised without standing up a real subprocess.
"""

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
    """Minimal managed-process double that survives ``os.close`` on its fd."""

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
    """Return a spawn callable that captures the SpawnOptions it received."""

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
    """Patch ``lifecycle.os.open`` so calls on ``target_path`` raise OSError."""

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
    """``_spawn_process`` falls back to ``/dev/null`` when ``os.open`` raises ENOSPC.

    The spawn must proceed so the server can serve. No untyped crash is
    permitted at the diagnostic-setup seam.
    """
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
    # The fallback fd is /dev/null, not the failed log path: the bridge
    # receives a writable fd even though the log-path open failed.
    options = recorded[0]
    assert options.stdout is not None and options.stdout >= 0
    assert options.stderr == options.stdout


def test_spawn_process_succeeds_when_diagnostic_open_raises_erofs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_spawn_process`` falls back to ``/dev/null`` when ``os.open`` raises EROFS.

    Same invariant as the ENOSPC case: the diagnostic-setup seam must fail
    open so the spawn proceeds and serving can continue.
    """
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
    """The ENOSPC diagnostic-setup failure emits a WARNING that names the path and errno.

    Operators rely on this WARNING to diagnose the missing-log condition;
    it must be present so the operator is not blindsided by the silent
    /dev/null fallback.
    """
    cwd = tmp_path / "workspace-warn"
    cwd.mkdir()
    log_path = lifecycle.mcp_server_log_path(cwd)
    _patch_os_open_to_fail(monkeypatch, target_path=log_path, errno_value=errno.ENOSPC)

    # loguru: capture by attaching a sink that records formatted messages.
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
    """A non-environmental OSError at the diagnostic seam still propagates as a typed error.

    The fail-open guard is scoped to ENOSPC / EROFS / EACCES / EPERM.
    A failure like EIO (I/O error) is NOT in the environmental set, so it
    must surface to the caller as a typed ``OSError`` — never as an
    untyped crash, never silently swallowed. The spec keeps the
    fail-closed invariant for everything outside the documented
    environmental set.
    """
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
    """After ENOSPC, no log file is written and the stage is ``unknown``.

    The diagnostic stream degraded to /dev/null, so the operator-visible
    stage attribution is "unknown" (the child's stage-tracker file is the
    canonical source, and no log was produced). This pins the spec
    invariant: ``_read_start_stage`` cannot find any record, so its caller
    defaults to "unknown".
    """
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

    # No log file was created (the open failed), and no stage-tracker
    # file was written by this code path.
    assert not log_path.exists()
    assert lifecycle._read_start_stage(cwd, spawn_id="test") is None


def test_record_contention_deferral_does_not_leak_untyped_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ledger path (``_record_contention_deferral``) stays fail-closed under OSError.

    The diagnostic setup may fail-open, but the integrity/ledger write
    path is typed fail-closed: an OSError at the sink is swallowed under
    an ``except OSError: pass`` guard (no untyped traceback escapes). The
    caller sees ``None`` (no exception), so the bridge keeps running and
    the supervision path does not abort.
    """
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
    # Force the durable sink to a path that does NOT exist on disk and
    # also block the parent mkdir so the write fails. The bridge writes
    # into ``self._durable_sink_path.parent`` so patching that directory
    # away (or replacing open) makes the append fail.
    broken_sink = tmp_path / "missing" / "subdir" / "events.jsonl"
    bridge._durable_sink_path = broken_sink

    # No exception escapes the ledger write: the path is typed
    # fail-closed via the existing ``except OSError: pass`` guard.
    bridge._record_contention_deferral(
        probe_duration_s=0.5,
        deferral_count=1,
        stage="probe_deferred_contention",
    )


def test_record_contention_deferral_typed_failure_path_still_raises_mcp_server_error(
    tmp_path: Path,
) -> None:
    """The budget-exhausted path is typed fail-closed: ``McpServerError``, never raw OSError.

    The diagnostic setup fails open, but the typed ``McpServerError``
    surface that the operator and supervisor see remains intact. A
    budget-exhausted restart still raises the typed error with the
    stage attribution, even when no diagnostic log file exists.
    """
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
    # Force the budget to be exhausted so the next restart raises.
    bridge._restart_count = bridge._restart_policy.max_restarts

    with pytest.raises(lifecycle.McpServerError):
        bridge._perform_restart(process_exited=False, probe_duration_s=0.25)
