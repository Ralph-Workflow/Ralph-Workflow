"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations

import io
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import ralph.mcp.tools._exec_completed_process as exec_completed_process
from ralph.mcp.tools import exec as exec_tool
from ralph.mcp.tools import exec_sandbox
from ralph.mcp.tools.exec import (
    ExecRunDeps,
    ExecutionError,
    run_command,
)
from ralph.process.manager import ProcessManager, ProcessManagerPolicy
from ralph.testing._process_state import ProcessState
from ralph.testing._process_streams import ProcessStreams
from ralph.testing.fake_process import FakePopen
from tests.mock_workspace_root import MockWorkspaceRoot

if TYPE_CHECKING:
    from collections.abc import Iterator

CUSTOM_TIMEOUT_MS = 5000
EXPECTED_TIMEOUT_SECONDS = 2.5
_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.0,
    kill_followup_timeout_s=0.0,
    log_events=False,
)


@contextmanager
def _passthrough_overlay(workspace_root: Path) -> Iterator[Path]:
    yield workspace_root


def test_posix_replaces_source_root_in_value() -> None:
    result = exec_tool._rewrite_env_path(
        "/home/user/proj/main.py",
        "/home/user/proj",
        "/overlay",
        "posix",
    )

    assert result == "/overlay/main.py"

def test_posix_passes_through_unrelated_value() -> None:
    value = "/etc/hosts"

    result = exec_tool._rewrite_env_path(value, "/home/user/proj", "/overlay", "posix")

    assert result == value

def test_windows_replaces_with_different_casing() -> None:
    result = exec_tool._rewrite_env_path(
        r"c:\users\user\proj\file.txt",
        r"C:\Users\User\proj",
        r"D:\overlay",
        "nt",
    )

    assert result == r"D:\overlay\file.txt"

def test_windows_passes_through_when_absent() -> None:
    value = r"C:\Windows\System32"

    result = exec_tool._rewrite_env_path(value, r"C:\proj", r"D:\overlay", "nt")

    assert result == value

def test_source_root_as_substring_is_also_replaced() -> None:
    result = exec_tool._rewrite_env_path(
        "/home/user/proj_old/data",
        "/home/user/proj",
        "/overlay",
        "posix",
    )

    assert result == "/overlay_old/data"


def test_successful_command(tmp_path: Path) -> None:
    result = run_command("echo", ["hello"], tmp_path, 5000)
    assert result.returncode == 0
    assert "hello" in result.stdout.decode()

def test_failing_command(tmp_path: Path) -> None:
    result = run_command("false", [], tmp_path, 5000)
    assert result.returncode != 0

def test_file_not_found_raises_execution_error(tmp_path: Path) -> None:
    with pytest.raises(ExecutionError):
        run_command("nonexistent_command_xyz", [], tmp_path, 5000)

def test_zero_timeout_means_no_timeout(tmp_path: Path) -> None:
    result = run_command("echo", ["test"], tmp_path, 0)
    assert result.returncode == 0

def test_workspace_with_str_root(tmp_path: Path) -> None:
    result = run_command("echo", ["test"], str(tmp_path), 5000)
    assert result.returncode == 0

def test_uses_injected_cwd_provider_when_workspace_has_no_root() -> None:
    seen: dict[str, object] = {}

    def fake_runner(
        command: list[str], cwd: Path, timeout_seconds: float | None
    ) -> exec_completed_process._CompletedProcessAdapter:
        seen["cwd"] = cwd
        return exec_completed_process._CompletedProcessAdapter(
            stdout=b"ok", stderr=b"", returncode=0
        )

    fallback = Path("/virtual/fallback")
    run_command(
        "python",
        ["--version"],
        object(),
        1000,
        deps=ExecRunDeps(
            runner=fake_runner,
            cwd_provider=lambda: fallback,
            overlay_factory=_passthrough_overlay,
        ),
    )

    assert seen["cwd"] == fallback

def test_uses_injected_runner(tmp_path: Path) -> None:
    seen: dict[str, object] = {}
    workspace = MockWorkspaceRoot(tmp_path)

    def fake_runner(
        command: list[str], cwd: Path, timeout_seconds: float | None
    ) -> exec_completed_process._CompletedProcessAdapter:
        seen["command"] = command
        seen["cwd"] = cwd
        seen["timeout"] = timeout_seconds
        return exec_completed_process._CompletedProcessAdapter(
            stdout=b"ok", stderr=b"", returncode=0
        )

    result = run_command(
        "python",
        ["--version"],
        workspace,
        2500,
        deps=ExecRunDeps(runner=fake_runner, overlay_factory=_passthrough_overlay),
    )

    assert result.returncode == 0
    assert seen["command"] == ["python", "--version"]
    assert seen["cwd"] == tmp_path
    assert seen["timeout"] == EXPECTED_TIMEOUT_SECONDS


def test_sets_pwd_to_overlay_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANYVAR", "value")

    env = exec_tool._child_process_env(tmp_path / "workspace", tmp_path / "overlay")

    assert env["PWD"] == str(tmp_path / "overlay")

def test_removes_oldpwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLDPWD", "/old")

    env = exec_tool._child_process_env(tmp_path / "workspace", tmp_path / "overlay")

    assert "OLDPWD" not in env

def test_replaces_source_root_in_env_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "workspace"
    overlay_root = tmp_path / "overlay"
    monkeypatch.setenv("SOMEVAR", f"{workspace_root}/sub")

    env = exec_tool._child_process_env(workspace_root, overlay_root)

    assert env["SOMEVAR"] == f"{overlay_root}/sub"

def test_passes_through_unrelated_env_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNRELATED", "/etc/hosts")

    env = exec_tool._child_process_env(tmp_path / "workspace", tmp_path / "overlay")

    assert env["UNRELATED"] == "/etc/hosts"


def test_run_command_round_robins_sandbox_paths(tmp_path: Path) -> None:
    seen_cwds: list[Path] = []

    def fake_runner(
        command: list[str], cwd: Path, timeout_seconds: float | None
    ) -> exec_completed_process._CompletedProcessAdapter:
        del command, timeout_seconds
        seen_cwds.append(cwd)
        (cwd / "dirty.txt").write_text("dirty", encoding="utf-8")
        return exec_completed_process._CompletedProcessAdapter(
            stdout=b"", stderr=b"", returncode=0
        )

    workspace = MockWorkspaceRoot(tmp_path)

    run_command("echo", [], workspace, 1000, deps=ExecRunDeps(runner=fake_runner))
    run_command("echo", [], workspace, 1000, deps=ExecRunDeps(runner=fake_runner))

    assert len(seen_cwds) == 2
    assert seen_cwds[0] != seen_cwds[1]


def test_run_command_uses_distinct_pool_slots_for_same_workspace_concurrent_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    monkeypatch.setattr(exec_tool, "_get_sandbox_manager", lambda _: manager)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = MockWorkspaceRoot(workspace_root)
    first_entered = threading.Event()
    second_entered = threading.Event()
    release_first = threading.Event()
    results: list[exec_completed_process._CompletedProcessAdapter] = []
    errors: list[BaseException] = []
    seen_cwds: list[Path] = []
    seen_lock = threading.Lock()

    def fake_runner(
        command: list[str], cwd: Path, timeout_seconds: float | None
    ) -> exec_completed_process._CompletedProcessAdapter:
        del command, timeout_seconds
        with seen_lock:
            seen_cwds.append(cwd)
            call_index = len(seen_cwds)
        if call_index == 1:
            first_entered.set()
            assert release_first.wait(timeout=1)
        else:
            second_entered.set()
        return exec_completed_process._CompletedProcessAdapter(
            stdout=b"ok", stderr=b"", returncode=0
        )

    def invoke() -> None:
        try:
            results.append(
                run_command("echo", [], workspace, 1000, deps=ExecRunDeps(runner=fake_runner))
            )
        except BaseException as exc:  # pragma: no cover - captured for assertion clarity
            errors.append(exc)

    first = threading.Thread(target=invoke)
    second = threading.Thread(target=invoke)

    first.start()
    assert first_entered.wait(timeout=1)
    second.start()
    assert second_entered.wait(timeout=0.2)
    release_first.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert not errors
    assert second_entered.is_set()
    assert len(results) == 2
    assert len(seen_cwds) == 2
    assert seen_cwds[0] != seen_cwds[1]


def test_run_command_kills_process_when_output_exceeds_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class StreamingFakePopen(FakePopen):
        def __init__(self) -> None:
            super().__init__(
                pid=101,
                state=ProcessState(returncode=None),
                streams=ProcessStreams(
                    stdout=io.BytesIO(b"prefix-1234567890-suffix"),
                    stderr=io.BytesIO(b"err-tail"),
                ),
            )

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            if self._returncode is None:
                self._returncode = 137 if (self._terminated or self._killed) else 0
            return self._returncode

    monkeypatch.setattr(exec_tool, "_MAX_OUTPUT_BYTES", 12)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=lambda command, opts: StreamingFakePopen(),
    )

    with pytest.raises(ExecutionError, match="killed after output exceeded 12 bytes") as excinfo:
        run_command(
            "python",
            ["-c", "print('boom')"],
            tmp_path,
            5_000,
            deps=ExecRunDeps(
                process_manager=pm,
                overlay_factory=_passthrough_overlay,
            ),
        )

    message = str(excinfo.value)
    assert "67890-suffix" in message
    assert "err-tail" in message
