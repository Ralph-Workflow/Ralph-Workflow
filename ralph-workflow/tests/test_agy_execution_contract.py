"""Tests for AGY execution contract: no session continuation, completion enforcement."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from ralph.agents.activity import AgentActivityKind
from ralph.agents.execution_state import AgentExecutionState, strategy_for_transport
from ralph.agents.idle_watchdog import (
    CorroborationSnapshot,
    IdleWatchdog,
    TimeoutPolicy,
    WaitingStatusEvent,
    WaitingStatusKind,
)
from ralph.agents.invoke import (
    AgentInvocationError,
    CompletionCheckOptions,
    check_process_result,
)
from ralph.agents.timeout_clock import FakeClock
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


def test_sentinel_check_fn_true_prevents_invocation_error(tmp_path: Path) -> None:
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
            captured_session_id="captured-run-id",
            completion_run_id="run-sentinel-id",
            _sentinel_check_fn=lambda workspace, run_id: (
                workspace == tmp_path and run_id == "run-sentinel-id"
            ),
        ),
    )


def test_sentinel_check_fn_false_still_raises_invocation_error(tmp_path: Path) -> None:
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
                captured_session_id="captured-run-id",
                completion_run_id="run-sentinel-id",
                _sentinel_check_fn=lambda workspace, run_id: False,
            ),
        )


def test_sentinel_check_fn_receives_completion_run_id(tmp_path: Path) -> None:
    strategy = strategy_for_transport(AgentTransport.AGY)
    handle = _FakeHandle(returncode=0)
    seen: list[tuple[Path, str | None]] = []

    def capture(workspace: Path, run_id: str | None) -> bool:
        seen.append((workspace, run_id))
        return True

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
            captured_session_id="captured-run-id",
            completion_run_id="run-sentinel-id",
            _sentinel_check_fn=capture,
        ),
    )

    assert seen == [(tmp_path, "run-sentinel-id")]


def test_sentinel_completion_without_pty_echo(tmp_path: Path) -> None:
    strategy = strategy_for_transport(AgentTransport.AGY)
    handle = _FakeHandle(returncode=0)
    sentinel = tmp_path / ".agent" / "completion_seen_observable-run-001.json"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text('{"run_id": "observable-run-001"}', encoding="utf-8")

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
            captured_session_id="parsed-session-001",
            completion_run_id="observable-run-001",
        ),
    )


def test_sentinel_absent_without_pty_echo_raises(tmp_path: Path) -> None:
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
                captured_session_id="parsed-session-001",
                completion_run_id="observable-run-001",
            ),
        )


def test_agy_classify_activity_line_json_is_output_not_lifecycle() -> None:
    strategy = strategy_for_transport(AgentTransport.AGY)
    signal = strategy.classify_activity_line('{"type": "message_start"}')
    assert signal is not None
    assert signal.kind == AgentActivityKind.OUTPUT_LINE


def test_agy_json_output_does_not_produce_lifecycle_only_watchdog_evidence() -> None:
    strategy = strategy_for_transport(AgentTransport.AGY)
    signal = strategy.classify_activity_line('{"type": "message_start"}')
    last_meaningful = signal is not None and signal.kind != AgentActivityKind.LIFECYCLE

    events: list[WaitingStatusEvent] = []
    policy = TimeoutPolicy(
        idle_timeout_seconds=1.0,
        max_waiting_on_child_seconds=1000.0,
        suspect_waiting_on_child_seconds=5.0,
        waiting_status_interval_seconds=100.0,
    )
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        policy,
        clock,
        listener=events.append,
        corroborator=lambda: CorroborationSnapshot(last_activity_was_meaningful=last_meaningful),
    )
    clock.advance(1.1)
    watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
    clock.advance(6.0)
    watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
    suspected = [e for e in events if e.kind == WaitingStatusKind.SUSPECTED_FROZEN]
    assert len(suspected) == 1
    evidence = str(suspected[0].diagnostic.get('evidence', ''))
    assert 'time_and_lifecycle_only' not in evidence
