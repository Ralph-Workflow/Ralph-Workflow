from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from ralph.agents.execution_state import ClaudeInteractiveExecutionStrategy
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    CompletionCheckOptions,
    OpenCodeResumableExitError,
    bounded_output_lines,
    check_process_result,
    extract_session_id,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.process.manager import ManagedPtyProcess


class _FakeInteractiveHandle:
    def __init__(self) -> None:
        self.returncode = 0

    def has_live_descendants(self) -> bool:
        return False


def test_extract_session_id_from_interactive_transcript_marker() -> None:
    output = ["Claude session ready. Session ID: pty-session-42\n"]

    assert extract_session_id(output) == "pty-session-42"


def test_bounded_output_does_not_treat_stop_hook_turn_boundary_as_explicit_completion() -> None:
    output = bounded_output_lines(["turn boundary seen"], explicit_completion_seen=False)

    assert output == ["turn boundary seen"]


def test_claude_interactive_resumable_exit_keeps_transcript_session_id(
    tmp_path: Path,
) -> None:
    handle = _FakeInteractiveHandle()
    strategy = ClaudeInteractiveExecutionStrategy()
    output = ["Resume this session with --resume pty-session-99\n"]

    with pytest.raises(OpenCodeResumableExitError) as excinfo:
        check_process_result(
            cast("ManagedPtyProcess", handle),
            "claude",
            output,
            CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                policy=TimeoutPolicy(
                    idle_timeout_seconds=None,
                    parent_exit_grace_seconds=0.0,
                    descendant_wait_poll_seconds=0.01,
                ),
            ),
        )

    assert excinfo.value.resumable_session_id == "pty-session-99"
