"""Tests for AGY execution contract: no session continuation, completion enforcement."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    AgentInvocationError,
    CompletionCheckOptions,
    check_process_result,
)
from ralph.config.enums import AgentTransport
from ralph.phases.required_artifacts import RequiredArtifact
from tests.fake_handle import _FakeHandle

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.process.manager import ManagedProcess


_check_process_result = check_process_result
_CompletionCheckOptions = CompletionCheckOptions


def test_agy_strategy_does_not_support_session_continuation() -> None:
    """AGY strategy reports supports_session_continuation() as False."""
    strategy = strategy_for_transport(AgentTransport.AGY)
    assert strategy.supports_session_continuation() is False


def test_agy_strategy_enforces_completion_evidence() -> None:
    """AGY strategy reports supports_completion_enforcement() as True."""
    strategy = strategy_for_transport(AgentTransport.AGY)
    assert strategy.supports_completion_enforcement() is True


def test_clean_exit_without_completion_signal_raises_agent_invocation_error(
    tmp_path: Path,
) -> None:
    """AGY exit-0 with no declare_complete and no artifact raises AgentInvocationError.

    This is non-retryable, so it does not create a retry loop.
    """
    strategy = strategy_for_transport(AgentTransport.AGY)
    handle = _FakeHandle(returncode=0)

    with pytest.raises(AgentInvocationError):
        _check_process_result(
            cast("ManagedProcess", handle),
            "agy",
            [],
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                required_artifact=RequiredArtifact(
                    phase="development",
                    artifact_type="development_result",
                    json_path=".agent/artifacts/development_result.json",
                    markdown_path=None,
                    normalizer=None,
                ),
                policy=TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0),
            ),
        )


def test_declare_complete_marker_satisfies_completion_contract(tmp_path: Path) -> None:
    """AGY raw output containing declare_complete marker does not raise."""
    strategy = strategy_for_transport(AgentTransport.AGY)
    handle = _FakeHandle(returncode=0)
    raw_output = ["Task declared complete: session_id=abc, summary=done, timestamp=1"]

    _check_process_result(
        cast("ManagedProcess", handle),
        "agy",
        raw_output,
        _CompletionCheckOptions(
            execution_strategy=strategy,
            workspace_path=tmp_path,
            required_artifact=RequiredArtifact(
                phase="development",
                artifact_type="development_result",
                json_path=".agent/artifacts/development_result.json",
                markdown_path=None,
                normalizer=None,
            ),
        ),
    )


def test_artifact_on_disk_satisfies_completion_contract(tmp_path: Path) -> None:
    """AGY with artifact on disk does not raise even without declare_complete."""
    artifact_dir = tmp_path / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "development_result.json").write_text('{"summary": "done"}')

    strategy = strategy_for_transport(AgentTransport.AGY)
    handle = _FakeHandle(returncode=0)

    _check_process_result(
        cast("ManagedProcess", handle),
        "agy",
        [],
        _CompletionCheckOptions(
            execution_strategy=strategy,
            workspace_path=tmp_path,
            required_artifact=RequiredArtifact(
                phase="development",
                artifact_type="development_result",
                json_path=".agent/artifacts/development_result.json",
                markdown_path=None,
                normalizer=None,
            ),
        ),
    )
