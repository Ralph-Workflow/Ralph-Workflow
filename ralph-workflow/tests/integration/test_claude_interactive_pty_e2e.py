from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ralph.agents.invoke import InvokeOptions, extract_session_id, invoke_agent
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig

pytestmark = pytest.mark.subprocess_e2e


@pytest.mark.timeout_seconds(5)
def test_claude_interactive_pty_runtime_behaves_like_tty_session(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("Ship the PTY runtime.", encoding="utf-8")
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "fake_claude_interactive_pty.py"
    config = AgentConfig(
        cmd=f"{sys.executable} {fixture}",
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
    assert extract_session_id(lines) == "pty-session-e2e"
