from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.invoke import (
    BuildCommandOptions,
    build_command,
)
from ralph.agents.registry import builtin_agents

if TYPE_CHECKING:
    from pathlib import Path


_DESCENDANT_WAIT_POLL_SECONDS = 0.5


class TestOpenCodeSessionReuse:
    def test_opencode_run_with_session_id_reuses_session(self, tmp_path: Path) -> None:
        config = builtin_agents()["opencode"]

        assert config.session_flag is not None, (
            "opencode builtin config must carry session_flag for session continuation"
        )

        prompt_file = tmp_path / "PROMPT.md"
        prompt_file.write_text("implement the task", encoding="utf-8")

        options = BuildCommandOptions(session_id="sess-x", workspace_path=tmp_path)
        cmd = build_command(config, "PROMPT.md", options=options)

        assert "--session" in cmd or "-s" in cmd, f"Session flag must appear in command: {cmd}"
        assert "sess-x" in cmd, f"Session ID must appear in command: {cmd}"
        assert cmd.index("sess-x") > 0, "Session ID must follow the session flag"
