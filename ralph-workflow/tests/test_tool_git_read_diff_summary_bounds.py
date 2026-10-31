"""Regression tests: summary-mode ``git_diff`` is bounded and runs no helpers.

Three defects in the summary path of ``handle_git_diff``:

- git's stderr was a pipe nobody read while stdout was streamed, so a git
  that writes more stderr than the pipe buffer blocked forever, and the
  stdout read loop had no deadline of its own;
- the read-only contract only rejected ``--ext-diff``/``--textconv`` flags,
  while git runs ``diff.external`` and textconv drivers from config by
  default;
- a diff of exactly ``max_bytes`` bytes was reported as truncated.

Every test drives the public handler with a fake process manager, so no
real git runs.
"""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.tools.coordination import ToolContent
from ralph.mcp.tools.git_read import GIT_DIFF_READ_CAPABILITY, handle_git_diff
from tests.mock_session import MockSession
from tests.mock_workspace_root import MockWorkspaceRoot

if TYPE_CHECKING:
    from pathlib import Path

#: Bound on every blocking wait inside the fakes, so the pre-fix code fails
#: these tests instead of hanging them.
_FAKE_WAIT_SECONDS = 2.0


@pytest.fixture(autouse=True)
def _resolve_cwd_without_git_probe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the handler hermetic despite the mandatory cwd probe."""
    monkeypatch.setattr(
        "ralph.mcp.tools.git_read.resolve_git_cwd",
        lambda **_kwargs: (tmp_path, False, None),
    )


class _BytesPipe:
    """Readable pipe over fixed bytes; sets ``drained`` once EOF is read."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0
        self.drained = threading.Event()

    def read(self, n: int = -1) -> bytes:
        if self._offset >= len(self._data):
            self.drained.set()
            return b""
        size = len(self._data) - self._offset if n < 0 else n
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class _StdoutBlockedOnStderr(_BytesPipe):
    """Stdout that yields nothing until stderr is drained.

    Models git blocked on a full stderr pipe: it cannot write stdout until
    somebody reads its stderr.
    """

    def __init__(self, data: bytes, stderr: _BytesPipe) -> None:
        super().__init__(data)
        self._stderr = stderr
        self.deadlocked = False

    def read(self, n: int = -1) -> bytes:
        if not self._stderr.drained.wait(timeout=_FAKE_WAIT_SECONDS):
            self.deadlocked = True
            return b""
        return super().read(n)


class _StdoutBlockedUntilKilled:
    """Stdout of a git that never writes and never exits on its own."""

    def __init__(self, killed: threading.Event) -> None:
        self._killed = killed

    def read(self, n: int = -1) -> bytes:
        self._killed.wait(timeout=_FAKE_WAIT_SECONDS)
        return b""


class _FakeProcess:
    def __init__(self, stdout: object, stderr: object, returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.killed = threading.Event()

    def communicate_and_cleanup(self, *args: object, **kwargs: object) -> tuple[bytes, bytes]:
        return (b"", b"")

    def kill(self) -> None:
        self.killed.set()


class _RecordingManager:
    """Fake process manager: numstat gets a fixed line, plain diff gets ``process``."""

    def __init__(self, process: _FakeProcess) -> None:
        self.process = process
        self.commands: list[list[str]] = []

    def spawn(self, command: list[str], *args: object, **kwargs: object) -> _FakeProcess:
        self.commands.append(list(command))
        if "--numstat" in command:
            return _FakeProcess(_BytesPipe(b"1\t0\ta.py\n"), _BytesPipe(b""))
        return self.process


def _summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    manager: _RecordingManager,
    max_bytes: int,
) -> tuple[bool, str]:
    monkeypatch.setattr("ralph.mcp.tools.git_read.get_process_manager", lambda: manager)
    result = handle_git_diff(
        MockSession({GIT_DIFF_READ_CAPABILITY}),
        MockWorkspaceRoot(tmp_path),
        {"format": "summary", "max_bytes": max_bytes},
    )
    content = result.content[0]
    assert isinstance(content, ToolContent)
    return result.is_error, content.text


@pytest.mark.timeout_seconds(2)
def test_summary_diff_drains_stderr_while_streaming_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stderr = _BytesPipe(b"w" * 200_000)
    stdout = _StdoutBlockedOnStderr(b"diff body\n", stderr)
    manager = _RecordingManager(_FakeProcess(stdout, stderr))

    is_error, text = _summary(monkeypatch, tmp_path, manager, 50_000)

    assert stdout.deadlocked is False
    assert is_error is False
    payload = json.loads(text)
    assert payload["diff_excerpt"] == "diff body\n"
    assert payload["truncated"] is False


@pytest.mark.timeout_seconds(2)
def test_summary_diff_times_out_and_kills_a_git_that_never_finishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    killed = threading.Event()
    process = _FakeProcess(_StdoutBlockedUntilKilled(killed), _BytesPipe(b""))
    process.killed = killed
    manager = _RecordingManager(process)
    monkeypatch.setattr("ralph.mcp.tools.git_read.GIT_READ_TIMEOUT_SECONDS", 0.2)

    is_error, text = _summary(monkeypatch, tmp_path, manager, 50_000)

    assert is_error is True
    assert "timed out" in text.lower()
    assert "do not retry" in text.lower()
    assert killed.is_set()


def test_every_diff_invocation_disables_external_helpers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _RecordingManager(_FakeProcess(_BytesPipe(b"body"), _BytesPipe(b"")))
    monkeypatch.setattr("ralph.mcp.tools.git_read.get_process_manager", lambda: manager)
    session = MockSession({GIT_DIFF_READ_CAPABILITY})
    workspace = MockWorkspaceRoot(tmp_path)

    handle_git_diff(session, workspace, {"args": ["--staged"]})
    handle_git_diff(session, workspace, {"args": ["--staged"], "format": "summary"})

    diff_commands = [command for command in manager.commands if command[:2] == ["git", "diff"]]
    # raw, then summary's --numstat pass and its capped excerpt pass.
    assert len(diff_commands) == 3
    for command in diff_commands:
        assert "--no-ext-diff" in command
        assert "--no-textconv" in command
        assert command.index("--no-ext-diff") < command.index("--staged")
        assert command.index("--no-textconv") < command.index("--staged")


@pytest.mark.parametrize(
    ("diff_size", "truncated"),
    [(10, False), (11, True)],
)
def test_summary_diff_reports_truncation_only_past_max_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    diff_size: int,
    truncated: bool,
) -> None:
    body = b"x" * diff_size
    manager = _RecordingManager(_FakeProcess(_BytesPipe(body), _BytesPipe(b"")))

    is_error, text = _summary(monkeypatch, tmp_path, manager, 10)

    assert is_error is False
    payload = json.loads(text)
    assert payload["truncated"] is truncated
    assert payload["diff_excerpt"] == "x" * 10
