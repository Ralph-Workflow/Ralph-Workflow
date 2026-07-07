from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents import invoke as invoke_module
from ralph.agents.catalog import default_catalog
from ralph.agents.invoke import InvokeOptions
from ralph.agents.registry import _seed_catalog_with_builtins, builtin_agents

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

from tests.test_claude_interactive_pty_helper__fakeptymanager import _FakePtyManager


@pytest.fixture(autouse=True)
def _seed_default_catalog() -> None:
    """Seed the default catalog with built-in agents for tests that depend on it.

    Tests in this file rely on ``default_catalog().get('claude')`` returning the
    built-in interactive Claude support, so that ``invoke_agent`` routes through
    the PTY runtime (the new spec.requires_pty dispatch in ralph/agents/invoke).
    The seeding runs in every worker process so parallel test execution does
    not depend on test ordering.
    """
    _seed_catalog_with_builtins(default_catalog())


def test_pending_vt_snapshot_line_surfaces_semantic_activity_without_newline() -> None:
    assert (
        invoke_module.pending_vt_snapshot_line("\x1b[2K\rclaude tool: write_file")
        == "claude tool: write_file"
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


def test_permission_prompt_line_ignores_bypass_status_indicator() -> None:
    status_line = (
        "\x1b[38;2;255;107;128m⏵⏵ bypass permissions on"
        "\x1b[38;2;153;153;153m (shift+tab to cycle) · ← for agents\x1b[39m"
    )

    assert invoke_module.is_permission_prompt_line(status_line) is False


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
        == "\r"
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
        yield '{"type":"session","session_id":"pty-session-1"}\n'
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
        '{"type":"session","session_id":"pty-session-1"}\n',
        "Task declared complete: session_id=pty-session-1, summary=done, timestamp=1\n",
    ]


def test_invoke_agent_does_not_invent_transcript_session_id_on_fresh_interactive_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("Implement the task.", encoding="utf-8")
    config = builtin_agents()["claude"]
    manager = _FakePtyManager()
    captured_expected_session_ids: list[str | None] = []

    def fake_run_pty_and_read_lines(
        _cmd: object,
        _ctx: object,
        extras: object = None,
    ) -> Iterator[str]:
        captured_expected_session_ids.append(getattr(extras, "expected_session_id", None))
        yield "Task declared complete: session_id=pty-session-1, summary=done, timestamp=1\n"

    monkeypatch.setattr(invoke_module, "get_process_manager", lambda: manager)
    monkeypatch.setattr(
        invoke_module,
        "run_pty_and_read_lines",
        fake_run_pty_and_read_lines,
    )

    list(
        invoke_module.invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                workspace_path=tmp_path,
                show_progress=False,
                # Skip the real WorkspaceMonitor watchdog observer:
                # this test only exercises routing / session-id
                # behaviour, so the observer's start/stop cost would
                # otherwise eat the 1-second per-test budget on a slow
                # machine.
                workspace_monitor_factory=lambda *args, **kwargs: None,
            ),
        )
    )

    assert captured_expected_session_ids == [None]


def test_invoke_agent_injects_nanocoder_prompt_path_into_interactive_pty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("Implement the Nanocoder task.", encoding="utf-8")
    config = builtin_agents()["nanocoder"]
    manager = _FakePtyManager()
    captured_cmds: list[list[str]] = []
    captured_initial_inputs: list[str | None] = []
    captured_ready_markers: list[tuple[str, ...]] = []

    def fake_run_pty_and_read_lines(
        cmd: list[str],
        _ctx: object,
        extras: object = None,
    ) -> Iterator[str]:
        captured_cmds.append(cmd)
        captured_initial_inputs.append(getattr(extras, "initial_input", None))
        captured_ready_markers.append(getattr(extras, "initial_input_ready_markers", ()))
        yield "Task declared complete: session_id=nanocoder-session, summary=done, timestamp=1\n"

    monkeypatch.setattr(invoke_module, "get_process_manager", lambda: manager)
    monkeypatch.setattr(invoke_module, "run_pty_and_read_lines", fake_run_pty_and_read_lines)

    list(
        invoke_module.invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                workspace_path=tmp_path,
                show_progress=False,
                workspace_monitor_factory=lambda *args, **kwargs: None,
            ),
        )
    )

    assert captured_cmds == [["nanocoder", "--mode", "yolo"]]
    assert captured_initial_inputs == [
        f"Read and follow the full task in {prompt_file}.\r"
    ]
    assert captured_ready_markers == [("What would you like me to help with?",)]
