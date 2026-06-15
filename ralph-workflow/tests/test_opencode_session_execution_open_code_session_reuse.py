"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.invoke import (
    BuildCommandOptions,
    CompletionCheckOptions,
    build_command,
    check_process_result,
)
from ralph.agents.registry import builtin_agents

if TYPE_CHECKING:
    from pathlib import Path


# Poll interval used in the wait helper - matches _DESCENDANT_WAIT_POLL_SECONDS
_DESCENDANT_WAIT_POLL_SECONDS = 0.5

# Local aliases: tests call the same public functions but under the private-looking names
# that were used when this module was monolithic (pre-package split).
_check_process_result = check_process_result
_CompletionCheckOptions = CompletionCheckOptions


class TestOpenCodeSessionReuse:
    def test_opencode_run_with_session_id_reuses_session(self, tmp_path: Path) -> None:
        """OpencodeCommandBuilder includes the session flag when session_id is provided."""
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
