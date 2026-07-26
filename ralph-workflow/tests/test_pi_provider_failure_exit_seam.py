"""Pi's rc=0 exit after an exhausted retry ladder must name the cause.

When the configured provider/model is unreachable, pi retries
internally (``auto_retry_start`` / ``auto_retry_end``) and then exits
**rc=0** having done nothing.  Before this contract the completion
gate saw only "no completion evidence" and raised the generic
:class:`OpenCodeResumableExitError`, so Ralph relaunched the agent
against the same dead provider indefinitely -- each retry prompt
carrying a growing wall of bodiless WARN lines and no cause.

The failure text pi reports (``message.errorMessage`` /
``auto_retry_end.finalError``) MUST reach the raised error so the
failure classifier can route it to connectivity backoff.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.agents.execution_state import OpenCodeExecutionStrategy
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    AgentInvocationError,
    CompletionCheckOptions,
    OpenCodeResumableExitError,
    check_process_result,
)
from ralph.phases.required_artifacts import RequiredArtifact
from tests.fake_handle import _FakeHandle

if TYPE_CHECKING:
    from pathlib import Path


_FAILED_MESSAGE: dict[str, object] = {
    "role": "assistant",
    "content": [],
    "provider": "codex-pooler",
    "model": "gpt-5.6-terra",
    "stopReason": "error",
    "errorMessage": "Connection error.",
}


def _options(tmp_path: Path) -> CompletionCheckOptions:
    return CompletionCheckOptions(
        execution_strategy=OpenCodeExecutionStrategy(),
        workspace_path=tmp_path,
        required_artifact=RequiredArtifact(
            phase="planning",
            artifact_type="planning_analysis_decision",
            artifact_path=".agent/artifacts/planning_analysis_decision.md",
            markdown_path=None,
            normalizer=None,
        ),
        policy=TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0),
    )


def test_pi_provider_failure_names_the_cause(tmp_path: Path) -> None:
    raw_output = [
        json.dumps({"type": "message_end", "message": _FAILED_MESSAGE}),
        json.dumps(
            {
                "type": "auto_retry_end",
                "success": False,
                "attempt": 3,
                "finalError": "Connection error.",
            }
        ),
    ]

    with pytest.raises(AgentInvocationError) as excinfo:
        check_process_result(
            _FakeHandle(returncode=0),
            "pi/codex-pooler/gpt-5.6-terra",
            raw_output,
            _options(tmp_path),
        )

    exc = excinfo.value
    assert not isinstance(exc, OpenCodeResumableExitError), (
        "a dead provider is not a resumable session; retrying the same "
        "session against the same provider loops forever"
    )
    assert "Connection error." in exc.stderr


def test_pi_agent_end_failure_also_names_the_cause(tmp_path: Path) -> None:
    """The failure may only ever appear on the ``agent_end`` messages array."""
    raw_output = [
        json.dumps({"type": "agent_end", "messages": [_FAILED_MESSAGE], "willRetry": False}),
    ]

    with pytest.raises(AgentInvocationError) as excinfo:
        check_process_result(
            _FakeHandle(returncode=0),
            "pi/codex-pooler/gpt-5.6-terra",
            raw_output,
            _options(tmp_path),
        )

    assert "Connection error." in excinfo.value.stderr


def test_non_pi_agent_is_unaffected(tmp_path: Path) -> None:
    """The pi-specific probe must not fire for other transports."""
    raw_output = [json.dumps({"type": "message_end", "message": _FAILED_MESSAGE})]

    with pytest.raises(OpenCodeResumableExitError):
        check_process_result(_FakeHandle(returncode=0), "opencode", raw_output, _options(tmp_path))


def test_pi_clean_run_without_evidence_stays_resumable(tmp_path: Path) -> None:
    """A pi run with no provider failure keeps the existing resumable path."""
    raw_output = [
        json.dumps(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "working"}],
                    "stopReason": "stop",
                },
            }
        )
    ]

    with pytest.raises(OpenCodeResumableExitError):
        check_process_result(
            _FakeHandle(returncode=0),
            "pi/codex-pooler/gpt-5.6-terra",
            raw_output,
            _options(tmp_path),
        )
