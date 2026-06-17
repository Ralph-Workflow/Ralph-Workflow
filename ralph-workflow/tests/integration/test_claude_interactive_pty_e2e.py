from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

from ralph.agents.invoke import InvokeOptions, extract_transport_session_id, invoke_agent
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig
from ralph.process.pty import spawn_pty_process

pytestmark = pytest.mark.subprocess_e2e


@pytest.mark.timeout_seconds(5)
def test_spawn_pty_process_closes_slave_fd_in_parent() -> None:
    proc = spawn_pty_process(
        [sys.executable, "-c", "pass"],
        cwd=None,
        env=None,
    )
    try:
        assert proc.slave_fd == -1
        proc.wait(timeout=2.0)
    finally:
        proc.close()


_REQUIRES_TTY_REASON = (
    "fixture fake_claude_interactive_pty.py raises SystemExit(91) when stdin/stdout "
    "are not TTYs; the test environment does not allocate a PTY for the test runner, "
    "so the fixture would exit 91 before producing any output. Run this test under "
    "a real PTY (e.g. ``script`` or a CI TTY allocation)."
)


@pytest.mark.timeout_seconds(5)
@pytest.mark.skipif(
    not sys.stdout.isatty() or not sys.stdin.isatty(),
    reason=_REQUIRES_TTY_REASON,
)
def test_claude_interactive_pty_runtime_behaves_like_tty_session(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("Ship the PTY runtime.", encoding="utf-8")
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "fake_claude_interactive_pty.py"
    config = AgentConfig(
        cmd=f"{shlex.quote(sys.executable)} {shlex.quote(str(fixture))}",
        output_flag=None,
        yolo_flag=None,
        json_parser=JsonParserType.CLAUDE,
        session_flag="--resume {}",
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )

    lines = list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(workspace_path=tmp_path, show_progress=False),
        )
    )

    assert any("claude tool: read_file" in line for line in lines)
    assert any("Task declared complete:" in line for line in lines)
    assert extract_transport_session_id(lines) == "pty-session-e2e"
