"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations

import io
import threading
from pathlib import Path

import pytest

import ralph.mcp.tools._exec_completed_process as exec_completed_process
import ralph.mcp.tools.exec as exec_tool
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

CUSTOM_TIMEOUT_MS = 5000
EXPECTED_TIMEOUT_SECONDS = 2.5
_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.0,
    kill_followup_timeout_s=0.0,
    log_events=False,
)


def test_run_command_writes_persist_in_workspace(tmp_path: Path) -> None:
    """File writes via run_command persist in the real workspace root (no sandbox)."""
    run_command("sh", ["-c", "touch created.txt"], tmp_path, 5000)
    assert (tmp_path / "created.txt").exists()


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
        deps=ExecRunDeps(runner=fake_runner),
    )

    assert result.returncode == 0
    assert seen["command"] == ["python", "--version"]
    assert seen["cwd"] == tmp_path
    assert seen["timeout"] == EXPECTED_TIMEOUT_SECONDS


def test_run_command_concurrent_calls_both_succeed(tmp_path: Path) -> None:
    """Concurrent run_command calls on the same workspace both complete without error."""
    workspace = MockWorkspaceRoot(tmp_path)
    results: list[exec_completed_process._CompletedProcessAdapter] = []
    errors: list[BaseException] = []

    first_entered = threading.Event()
    release_first = threading.Event()

    def fake_runner_first(
        command: list[str], cwd: Path, timeout_seconds: float | None
    ) -> exec_completed_process._CompletedProcessAdapter:
        del command, cwd, timeout_seconds
        first_entered.set()
        assert release_first.wait(timeout=1)
        return exec_completed_process._CompletedProcessAdapter(
            stdout=b"ok", stderr=b"", returncode=0
        )

    def fake_runner_second(
        command: list[str], cwd: Path, timeout_seconds: float | None
    ) -> exec_completed_process._CompletedProcessAdapter:
        del command, cwd, timeout_seconds
        return exec_completed_process._CompletedProcessAdapter(
            stdout=b"ok", stderr=b"", returncode=0
        )

    def invoke_first() -> None:
        try:
            results.append(
                run_command("echo", [], workspace, 1000, deps=ExecRunDeps(runner=fake_runner_first))
            )
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    def invoke_second() -> None:
        try:
            results.append(
                run_command(
                    "echo", [], workspace, 1000, deps=ExecRunDeps(runner=fake_runner_second)
                )
            )
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    t1 = threading.Thread(target=invoke_first)
    t2 = threading.Thread(target=invoke_second)
    t1.start()
    assert first_entered.wait(timeout=1)
    t2.start()
    t2.join(timeout=1)
    release_first.set()
    t1.join(timeout=1)

    assert not errors
    assert len(results) == 2


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
            deps=ExecRunDeps(process_manager=pm),
        )

    message = str(excinfo.value)
    assert "67890-suffix" in message
    assert "err-tail" in message


class _ChunkedStream:
    """Fake IO[bytes] stream that returns fixed-size chunks for deterministic testing."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    def read(self, n: int) -> bytes:
        del n
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def close(self) -> None:
        pass

    def __iter__(self) -> _ChunkedStream:
        return self

    def __next__(self) -> bytes:
        chunk = self.read(8_192)
        if not chunk:
            raise StopIteration
        return chunk


def test_run_subprocess_streams_output_chunks_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that on_output_chunk receives chunks in order before run_command returns."""

    class ChunkedFakePopen(FakePopen):
        def __init__(self) -> None:
            super().__init__(
                pid=201,
                state=ProcessState(returncode=0),
                streams=ProcessStreams(
                    stdout=_ChunkedStream([b"first-chunk", b"second-chunk"]),
                    stderr=_ChunkedStream([b"err-chunk"]),
                ),
            )

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            if self._returncode is None:
                self._returncode = 0
            return self._returncode

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=lambda command, opts: ChunkedFakePopen(),
    )
    received_chunks: list[str] = []

    result = run_command(
        "echo",
        ["hello"],
        tmp_path,
        5_000,
        deps=ExecRunDeps(
            process_manager=pm,
            on_output_chunk=received_chunks.append,
        ),
    )

    assert received_chunks, "on_output_chunk must be called at least once with output"
    assert received_chunks.index("first-chunk") < len(received_chunks), (
        "first-chunk must be received before run_command returns"
    )
    assert "second-chunk" in received_chunks, (
        "second-chunk must be received before run_command returns"
    )
    combined = "".join(received_chunks)
    assert "first-chunk" in combined
    assert "second-chunk" in combined
    assert result.stdout == b"first-chunksecond-chunk"
