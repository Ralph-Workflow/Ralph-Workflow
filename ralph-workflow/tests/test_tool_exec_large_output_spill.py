"""Oversized exec output spills to a file instead of being killed/discarded.

When a command produces more output than fits inline, exec must not throw the
output away (the old behavior killed the process at the byte cap and returned an
error with no usable output, forcing the agent into a retry loop). Instead it
writes the full output to a temp file and returns a bounded head/tail preview
plus the path, so the agent can read it in chunks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.tools._exec_completed_process import _CompletedProcessAdapter
from ralph.mcp.tools.coordination import ToolContent
from ralph.mcp.tools.exec import ExecRunDeps, handle_exec_command
from tests.mock_session import MockSession
from tests.mock_workspace_root import MockWorkspaceRoot

if TYPE_CHECKING:
    from pathlib import Path

_FIRST_LINE = "line-00000000"
_LAST_LINE = "line-00149999"


def _large_body() -> bytes:
    return "".join(f"line-{i:08d}\n" for i in range(150_000)).encode()


def test_large_output_spills_to_file_with_preview(tmp_path: Path) -> None:
    session = MockSession({"ProcessExecBounded"})
    workspace = MockWorkspaceRoot(tmp_path)
    spill_dir = tmp_path / "spill"
    spill_dir.mkdir()
    body = _large_body()

    def fake_runner(
        _argv: list[str], _cwd: Path, _timeout: float | None
    ) -> _CompletedProcessAdapter:
        return _CompletedProcessAdapter(stdout=body, stderr=b"", returncode=0)

    deps = ExecRunDeps(runner=fake_runner, spill_dir=spill_dir)
    result = handle_exec_command(session, workspace, {"command": "make verify"}, deps=deps)

    # A successful (returncode 0) command is not a hard error just because it was big.
    assert result.is_error is False
    content = result.content[0]
    assert isinstance(content, ToolContent)
    text = content.text

    # The inline payload is bounded — the whole 2MB body is NOT dumped into context.
    assert len(text) < len(body)
    assert len(text) < 1_000_000

    # Exactly one spill file was written under the injected dir, and its path is
    # surfaced to the agent.
    spill_files = list(spill_dir.iterdir())
    assert len(spill_files) == 1
    spilled = spill_files[0]
    assert str(spilled) in text

    # The preview shows both ends so the agent can see head and tail without reading.
    assert _FIRST_LINE in text
    assert _LAST_LINE in text

    # The spill file holds the full output.
    contents = spilled.read_text()
    assert _FIRST_LINE in contents
    assert _LAST_LINE in contents


def test_small_output_stays_inline_and_does_not_spill(tmp_path: Path) -> None:
    session = MockSession({"ProcessExecBounded"})
    workspace = MockWorkspaceRoot(tmp_path)
    spill_dir = tmp_path / "spill"
    spill_dir.mkdir()

    def fake_runner(
        _argv: list[str], _cwd: Path, _timeout: float | None
    ) -> _CompletedProcessAdapter:
        return _CompletedProcessAdapter(stdout=b"hello world\n", stderr=b"", returncode=0)

    deps = ExecRunDeps(runner=fake_runner, spill_dir=spill_dir)
    result = handle_exec_command(session, workspace, {"command": "echo hello world"}, deps=deps)

    assert result.is_error is False
    content = result.content[0]
    assert isinstance(content, ToolContent)
    assert "hello world" in content.text
    assert list(spill_dir.iterdir()) == []
