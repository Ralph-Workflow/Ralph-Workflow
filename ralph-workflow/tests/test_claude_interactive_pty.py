from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents import invoke as invoke_module
from ralph.agents.invoke import InvokeOptions
from ralph.agents.registry import builtin_agents

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


class _FakePtyHandle:
    def __init__(self) -> None:
        self.record = type("Record", (), {"pid": 321, "status": None})()
        self.returncode = 0
        self.master_fd = 77

    def __enter__(self) -> _FakePtyHandle:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0


class _FakePtyManager:

    def __init__(self) -> None:
        self.spawn_called = False
        self.spawn_pty_called = False

    def spawn(self, *args: object, **kwargs: object) -> _FakePtyHandle:
        del args, kwargs
        self.spawn_called = True
        pytest.fail("interactive Claude must not use pipe-based spawn()")

    def spawn_pty(self, *args: object, **kwargs: object) -> _FakePtyHandle:
        del args, kwargs
        self.spawn_pty_called = True
        return _FakePtyHandle()




def test_pending_vt_snapshot_line_surfaces_semantic_activity_without_newline() -> None:
    assert (
        invoke_module.pending_vt_snapshot_line("\x1b[2K\rclaude tool: write_file")
        == "claude tool: write_file\n"
    )


def test_permission_prompt_line_is_detected() -> None:
    assert (
        invoke_module.is_permission_prompt_line(
            "Claude requested permissions to read from /tmp/prompt.md"
        )
        is True
    )
    assert invoke_module.is_permission_prompt_line("Enable auto mode?") is True
    assert (
        invoke_module.is_permission_prompt_line(
            "\u276f 1. Yes, and make it my default mode\n2. Yes, enable auto mode\nEnter to confirm"
        )
        is True
    )
    assert invoke_module.is_permission_prompt_line("claude tool: write_file") is False


def test_auto_response_for_interactive_prompt_handles_auto_mode_gate() -> None:
    menu = """
    Enable auto mode?

    \u276f 1. Yes, and make it my default mode
      2. Yes, enable auto mode

    Enter to confirm · Esc to cancel
    """
    assert (
        invoke_module.interactive_auto_response_for_prompt(
            menu,
            auto_mode_prompt_seen=True,
        )
        == "\x1b[B\r"
    )
    assert (
        invoke_module.interactive_auto_response_for_prompt(
            "Enable auto mode?", auto_mode_prompt_seen=False
        )
        is None
    )
    assert (
        invoke_module.interactive_auto_response_for_prompt(
            "claude tool: write_file", auto_mode_prompt_seen=False
        )
        is None
    )


def test_invoke_agent_routes_claude_interactive_through_pty_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("Implement the task.", encoding="utf-8")
    config = builtin_agents()["claude"]
    manager = _FakePtyManager()

    def fake_run_pty_and_read_lines(*args: object, **kwargs: object) -> Iterator[str]:
        del args, kwargs
        yield '{"session_id":"pty-session-1"}\n'
        yield "Task declared complete: session_id=pty-session-1, summary=done, timestamp=1\n"

    monkeypatch.setattr(invoke_module, "get_process_manager", lambda: manager)
    monkeypatch.setattr(
        invoke_module,
        "run_pty_and_read_lines",
        fake_run_pty_and_read_lines,
    )

    lines = list(
        invoke_module.invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(workspace_path=tmp_path, show_progress=False),
        )
    )

    assert manager.spawn_called is False
    assert manager.spawn_pty_called is False
    assert lines == [
        '{"session_id":"pty-session-1"}\n',
        "Task declared complete: session_id=pty-session-1, summary=done, timestamp=1\n",
    ]
