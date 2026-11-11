from __future__ import annotations

import asyncio
import itertools
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from ralph.executor import ProcessExecutionError, ProcessResult, run_process, run_process_async
from ralph.executor.process import ProcessRunOptions
from ralph.process.manager import ProcessManager, ProcessManagerPolicy, ProcessStatus, SpawnOptions
from ralph.testing._fake_async_process import FakeAsyncProcess
from ralph.testing._process_state import ProcessState
from ralph.testing.fake_process import FakeControllableAsyncProcess, FakePopen, FakeTimeoutPopen

if TYPE_CHECKING:
    from pathlib import Path

PYTHON = sys.executable
EXIT_CODE = 3
TIMEOUT_S = 0.5
SYNC_FAILURE_SCRIPT = (
    f"import sys; print('hello'); print('oops', file=sys.stderr); raise SystemExit({EXIT_CODE})"
)

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.0,
    kill_followup_timeout_s=0.0,
    log_events=False,
    enable_zombie_reaper=False,
)


def _make_timeout_pm(partial_stdout: bytes = b"") -> ProcessManager:
    pid_iter = itertools.count(1)

    def factory(command: object, opts: SpawnOptions) -> object:
        return FakeTimeoutPopen(next(pid_iter), partial_stdout=partial_stdout)

    return ProcessManager(policy=_FAST_POLICY, sync_process_factory=factory)


class _FakeRaisingPopen:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._returncode: int | None = None
        self.terminate_calls = 0
        self.wait_calls: list[float | None] = []
        self.stdin: object = None
        self.stdout: object = None
        self.stderr: object = None

    def poll(self) -> int | None:
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        self._returncode = 0
        return 0

    def communicate(
        self,
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes, bytes]:
        del input, timeout
        raise OSError("broken pipe")

    def terminate(self) -> None:
        self.terminate_calls += 1
        self._returncode = -15

    def kill(self) -> None:
        self._returncode = -9


class _FakeRaisingAsyncProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._returncode: int | None = None
        self.terminate_calls = 0
        self.wait_calls = 0
        self.stdin: object = None
        self.stdout: object = None
        self.stderr: object = None

    @property
    def returncode(self) -> int | None:
        return self._returncode

    async def wait(self) -> int:
        self.wait_calls += 1
        self._returncode = 0
        return 0

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        del input
        raise OSError("broken pipe")

    def terminate(self) -> None:
        self.terminate_calls += 1
        self._returncode = -15

    def kill(self) -> None:
        self._returncode = -9


@pytest.mark.timeout_seconds(5)
def test_run_process_captures_stdout_stderr_and_exit_code(tmp_path: Path) -> None:
    result = run_process(
        PYTHON, ["-c", SYNC_FAILURE_SCRIPT], options=ProcessRunOptions(cwd=tmp_path)
    )

    assert isinstance(result, ProcessResult)
    assert result.returncode == EXIT_CODE
    assert result.succeeded is False
    assert result.stdout.strip() == "hello"
    assert result.stderr.strip() == "oops"
    assert result.command == (PYTHON, "-c", SYNC_FAILURE_SCRIPT)


@pytest.mark.asyncio
@pytest.mark.timeout_seconds(5)
async def test_run_process_async_captures_output(tmp_path: Path) -> None:
    result = await run_process_async(
        PYTHON,
        ["-c", "print('async hello')"],
        cwd=tmp_path,
    )

    assert result.succeeded is True
    assert result.returncode == 0
    assert result.stdout.strip() == "async hello"
    assert result.stderr == ""


def test_run_process_timeout_includes_context(tmp_path: Path) -> None:
    partial = b"before-timeout"
    pm = _make_timeout_pm(partial_stdout=partial)

    result = run_process(
        "fake-cmd",
        options=ProcessRunOptions(cwd=tmp_path, timeout=TIMEOUT_S),
        _pm=pm,
    )

    assert isinstance(result, ProcessResult)
    assert result.returncode == 124
    assert result.succeeded is False
    assert result.stdout.strip() == "before-timeout"
    assert result.command == ("fake-cmd",)


class _RecordingPostTerminatePopen:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._returncode: int | None = None
        self._communicate_count = 0
        self.communicate_timeouts: list[float | None] = []
        self.terminate_calls = 0
        self.stdin: object = None
        self.stdout: object = None
        self.stderr: object = None

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def poll(self) -> int | None:
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self._returncode if self._returncode is not None else 0

    def communicate(
        self,
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes, bytes]:
        del input
        self.communicate_timeouts.append(timeout)
        self._communicate_count += 1
        if self._communicate_count == 1 and timeout is not None:
            raise subprocess.TimeoutExpired(
                cmd="fake-recording",
                timeout=timeout,
                output=b"",
                stderr=b"",
            )
        return b"", b""

    def terminate(self) -> None:
        self.terminate_calls += 1
        self._returncode = -15

    def kill(self) -> None:
        self._returncode = -9


def test_run_process_post_terminate_drain_is_bounded(tmp_path: Path) -> None:
    fake = _RecordingPostTerminatePopen(pid=1)

    def factory(command: object, opts: SpawnOptions) -> object:
        del command, opts
        return fake

    pm = ProcessManager(policy=_FAST_POLICY, sync_process_factory=factory)

    result = run_process(
        "fake-cmd",
        options=ProcessRunOptions(cwd=tmp_path, timeout=TIMEOUT_S),
        _pm=pm,
    )

    assert result.returncode == 124
    assert len(fake.communicate_timeouts) == 2
    assert fake.communicate_timeouts[0] == TIMEOUT_S
    assert fake.communicate_timeouts[1] is not None
    assert isinstance(fake.communicate_timeouts[1], float)
    assert fake.communicate_timeouts[1] > 0


@pytest.mark.asyncio
async def test_run_process_async_timeout_includes_context(tmp_path: Path) -> None:

    completion = asyncio.Event()
    proc = FakeControllableAsyncProcess(
        pid=1,
        stdout_data=b"before-async-timeout",
        completion_event=completion,
    )

    async def factory(
        command: object,
        *,
        cwd: object,
        env: object,
        stdin: object,
        stdout: object,
        stderr: object,
        start_new_session: object,
    ) -> object:
        del command, cwd, env, stdin, stdout, stderr, start_new_session
        return proc

    pm = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.0, kill_followup_timeout_s=0.1, log_events=False
        ),
        async_process_factory=factory,
    )

    result = await run_process_async(
        "fake-cmd",
        cwd=tmp_path,
        timeout=0.0,
        _pm=pm,
    )

    assert isinstance(result, ProcessResult)
    assert result.returncode == 124
    assert result.succeeded is False


def test_run_process_wraps_missing_command(tmp_path: Path) -> None:
    missing_command = "definitely-not-a-real-command-ralph"

    with pytest.raises(ProcessExecutionError) as excinfo:
        run_process(missing_command, options=ProcessRunOptions(cwd=tmp_path))

    error = excinfo.value
    assert error.timed_out is False
    assert error.command == (missing_command,)
    assert error.__cause__ is not None


def test_run_process_cleans_up_on_non_timeout_communicate_exception(
    tmp_path: Path,
) -> None:
    fake = _FakeRaisingPopen(pid=1)

    def factory(command: object, opts: SpawnOptions) -> object:
        del command, opts
        return fake

    pm = ProcessManager(policy=_FAST_POLICY, sync_process_factory=factory)

    with pytest.raises(OSError, match="broken pipe"):
        run_process("fake-cmd", options=ProcessRunOptions(cwd=tmp_path), _pm=pm)

    records = pm.list_records(include_active=True, include_terminal=True)
    assert len(records) == 1
    assert records[0].status == ProcessStatus.KILLED
    assert fake.terminate_calls == 1


def test_run_process_records_default_label() -> None:
    pm = _make_timeout_pm(partial_stdout=b"hello")
    result = run_process("fake-cmd", options=ProcessRunOptions(), _pm=pm)
    assert result.stdout == "hello"
    records = pm.list_records(
        include_active=True, include_terminal=True, label_prefix="executor:run-process"
    )
    assert len(records) == 1, (
        f"expected exactly one executor:run-process record, got {len(records)}"
    )
    assert records[0].label == "executor:run-process"


@pytest.mark.asyncio
async def test_run_process_async_records_default_label() -> None:
    pid_iter = itertools.count(100)

    async def factory(
        command: object,
        *,
        cwd: object,
        env: object,
        stdin: object,
        stdout: object,
        stderr: object,
        start_new_session: object,
    ) -> object:
        del command, cwd, env, stdin, stdout, stderr, start_new_session
        return FakeAsyncProcess(next(pid_iter), state=ProcessState(returncode=0))

    pm = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.5,
            kill_followup_timeout_s=0.5,
            log_events=False,
            enable_zombie_reaper=False,
        ),
        async_process_factory=factory,
    )
    result = await run_process_async("fake-cmd", _pm=pm)
    assert result.returncode == 0
    records = pm.list_records(
        include_active=True, include_terminal=True, label_prefix="executor:run-process"
    )
    assert len(records) == 1, (
        f"expected exactly one executor:run-process record, got {len(records)}"
    )
    assert records[0].label == "executor:run-process"


def test_run_process_custom_label_overrides_default() -> None:
    pm = _make_timeout_pm(partial_stdout=b"")
    custom_label = "executor:test-explicit-label"
    run_process(
        "fake-cmd",
        options=ProcessRunOptions(label=custom_label),
        _pm=pm,
    )
    records = pm.list_records(include_active=True, include_terminal=True, label_prefix=custom_label)
    assert len(records) == 1
    assert records[0].label == custom_label
    default_records = pm.list_records(
        include_active=True, include_terminal=True, label_prefix="executor:run-process"
    )
    assert all(r.label != custom_label for r in default_records), (
        "records with custom label must not appear under the default prefix scan"
    )


@pytest.mark.asyncio
async def test_run_process_async_cleans_up_on_non_timeout_communicate_exception(
    tmp_path: Path,
) -> None:
    fake = _FakeRaisingAsyncProcess(pid=1)

    async def factory(
        command: object,
        *,
        cwd: object,
        env: object,
        stdin: object,
        stdout: object,
        stderr: object,
        start_new_session: object,
    ) -> object:
        del command, cwd, env, stdin, stdout, stderr, start_new_session
        return fake

    pm = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.5,
            kill_followup_timeout_s=0.5,
            log_events=False,
            enable_zombie_reaper=False,
        ),
        async_process_factory=factory,
    )

    with pytest.raises(OSError, match="broken pipe"):
        await run_process_async("fake-cmd", cwd=tmp_path, _pm=pm)

    records = pm.list_records(include_active=True, include_terminal=True)
    assert len(records) == 1
    assert records[0].status == ProcessStatus.KILLED
    assert fake.terminate_calls == 1


_FOREIGN_FAMILY_LABEL = "agents:agy-models"


@pytest.mark.timeout_seconds(5)
def test_run_process_reaps_child_spawned_under_foreign_family_label(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING", logger="ralph.process.teardown"):
        result = run_process(
            PYTHON,
            ["-c", "print('ok')"],
            options=ProcessRunOptions(cwd=tmp_path, label=_FOREIGN_FAMILY_LABEL),
        )

    assert result.stdout.strip() == "ok"
    assert "refusing process teardown" not in caplog.text


@pytest.mark.asyncio
async def test_run_process_async_reaps_child_spawned_under_foreign_family_label(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING", logger="ralph.process.teardown"):
        result = await run_process_async(
            PYTHON, ["-c", "print('ok')"], cwd=tmp_path, label=_FOREIGN_FAMILY_LABEL
        )

    assert result.stdout.strip() == "ok"
    assert "refusing process teardown" not in caplog.text


def _make_env_recording_pm(spawned_envs: list[dict[str, str] | None]) -> ProcessManager:
    pid_iter = itertools.count(1)

    def factory(command: object, opts: SpawnOptions) -> object:
        del command
        spawned_envs.append(opts.env)
        return FakePopen(next(pid_iter), state=ProcessState(returncode=0))

    return ProcessManager(policy=_FAST_POLICY, sync_process_factory=factory)


def test_run_process_env_remove_drops_inherited_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/source/checkout")
    monkeypatch.setenv("RALPH_KEEP_ME", "kept")
    spawned_envs: list[dict[str, str] | None] = []

    run_process(
        "fake-cmd",
        options=ProcessRunOptions(
            env={"RALPH_EXTRA": "extra"}, env_remove=frozenset({"PYTHONPATH"})
        ),
        _pm=_make_env_recording_pm(spawned_envs),
    )

    [child_env] = spawned_envs
    assert child_env is not None
    assert "PYTHONPATH" not in child_env
    assert child_env["RALPH_KEEP_ME"] == "kept"
    assert child_env["RALPH_EXTRA"] == "extra"


def test_process_run_options_rejects_setting_and_removing_the_same_variable() -> None:
    with pytest.raises(ValueError, match="PYTHONPATH"):
        ProcessRunOptions(env={"PYTHONPATH": "/x"}, env_remove=frozenset({"PYTHONPATH"}))
