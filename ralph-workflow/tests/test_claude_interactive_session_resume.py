from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.execution_state import ClaudeInteractiveExecutionStrategy
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    CompletionCheckOptions,
    OpenCodeResumableExitError,
    bounded_output_lines,
    check_process_result,
    extract_transport_session_id,
)
from ralph.agents.invoke._process_reader import _agent_command_name
from ralph.agents.registry import AgentRegistry
from ralph.config.models import UnifiedConfig

if TYPE_CHECKING:
    from pathlib import Path


class _FakeInteractiveHandle:
    def __init__(self) -> None:
        self.returncode = 0

    def has_live_descendants(self) -> bool:
        return False


def test_extract_session_id_from_interactive_transcript_marker() -> None:
    output = ["Claude session ready. Session ID: pty-session-42\n"]

    assert extract_transport_session_id(output) == "pty-session-42"


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
            handle,
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


@pytest.mark.parametrize("alias", ["opus", "sonnet"])
def test_claude_opus_or_sonnet_resumable_exit_when_no_completion_evidence(
    alias: str,
    tmp_path: Path,
) -> None:
    """``claude/opus`` and ``claude/sonnet`` rc=0 exits without evidence stay resumable.

    Regression pin for the production failure where ``claude/opus`` exited
    cleanly (rc=0) with no artifact (``.agent/artifacts/plan.md absent``).
    The PTY runner passes the runtime agent name derived by
    ``_agent_command_name(ctx.config)`` to ``check_process_result``; for
    every ``claude/<alias>`` dynamic alias the resolved ``cmd`` is
    ``"claude"``, so the completion gate must classify the clean exit as
    resumable -- raising ``OpenCodeResumableExitError`` with the session id
    captured from the transcript marker -- instead of letting the alias
    exit silently with no artifact and no resubmit hint.
    """
    handle = _FakeInteractiveHandle()
    registry = AgentRegistry.from_config(UnifiedConfig())
    resolved = registry.get(f"claude/{alias}")

    assert resolved is not None
    runtime_name = _agent_command_name(resolved)
    assert runtime_name == "claude"
    output = [f"Resume this session with --resume pty-alias-{alias}\n"]

    with pytest.raises(OpenCodeResumableExitError) as excinfo:
        check_process_result(
            handle,
            runtime_name,
            output,
            CompletionCheckOptions(
                execution_strategy=ClaudeInteractiveExecutionStrategy(),
                workspace_path=tmp_path,
                policy=TimeoutPolicy(
                    idle_timeout_seconds=None,
                    parent_exit_grace_seconds=0.0,
                    descendant_wait_poll_seconds=0.01,
                ),
            ),
        )

    assert excinfo.value.resumable_session_id == f"pty-alias-{alias}"
