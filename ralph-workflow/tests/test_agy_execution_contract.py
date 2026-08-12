"""Tests for AGY execution contract: no session continuation, completion enforcement."""

from __future__ import annotations

from typing import TYPE_CHECKING

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




def test_agy_strategy_does_not_support_session_continuation() -> None:
    """AGY strategy reports supports_session_continuation() as False."""
    strategy = strategy_for_transport(AgentTransport.AGY)
    assert strategy.supports_session_continuation() is False


def test_agy_strategy_enforces_completion_evidence() -> None:
    """AGY strategy reports supports_completion_enforcement() as True."""
    strategy = strategy_for_transport(AgentTransport.AGY)
    assert strategy.supports_completion_enforcement() is True


def test_agy_empty_output_diagnostic_retains_missing_artifact_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An AGY operator diagnosis supplements, rather than hides, completion failure."""
    monkeypatch.setattr(
        "ralph.agents.invoke._completion.agy_empty_output_reason",
        lambda _output, *, cli_log_path: "AGY authentication failed",
    )

    with pytest.raises(AgentInvocationError) as excinfo:
        check_process_result(
            _FakeHandle(returncode=0),
            "agy",
            [],
            CompletionCheckOptions(
                execution_strategy=strategy_for_transport(AgentTransport.AGY),
                workspace_path=tmp_path,
                required_artifact=RequiredArtifact(
                    phase="development",
                    artifact_type="development_result",
                    artifact_path=".agent/artifacts/development_result.md",
                    markdown_path=None,
                    normalizer=None,
                ),
                agy_cli_log_path=tmp_path / "unused.log",
            ),
        )

    assert "authentication failed" in str(excinfo.value)
    assert "required artifact receipt missing" in str(excinfo.value)


def test_clean_exit_without_completion_signal_raises_agent_invocation_error(
    tmp_path: Path,
) -> None:
    """AGY exit-0 with no declare_complete and no artifact raises AgentInvocationError.

    This is non-retryable, so it does not create a retry loop.
    """
    strategy = strategy_for_transport(AgentTransport.AGY)
    handle = _FakeHandle(returncode=0)

    with pytest.raises(AgentInvocationError):
        check_process_result(
            handle,
            "agy",
            [],
            CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                required_artifact=RequiredArtifact(
                    phase="development",
                    artifact_type="development_result",
                    artifact_path=".agent/artifacts/development_result.md",
                    markdown_path=None,
                    normalizer=None,
                ),
                policy=TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0),
            ),
        )


def test_declare_complete_sentinel_satisfies_artifact_free_completion_contract(
    tmp_path: Path,
) -> None:
    """AGY durable declaration is terminal when the phase has no artifact contract.

    The plain-text marker alone is no longer authoritative: it can be spoofed
    by ordinary agent output. The completion sentinel written by the real
    declare_complete MCP tool provides the required corroboration.
    """
    strategy = strategy_for_transport(AgentTransport.AGY)
    handle = _FakeHandle(returncode=0)
    raw_output = ["Task declared complete: session_id=abc, summary=done, timestamp=1"]
    sentinel = tmp_path / ".agent" / "completion_seen_abc.json"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text('{"run_id": "abc"}', encoding="utf-8")

    check_process_result(
        handle,
        "agy",
        raw_output,
        CompletionCheckOptions(
            execution_strategy=strategy,
            workspace_path=tmp_path,
            captured_session_id="abc",
        ),
    )


def test_required_artifact_receipt_needs_completion_sentinel(tmp_path: Path) -> None:
    """AGY required-artifact completion needs receipt and sentinel.

    The legacy schema-validity fallback for on-disk canonical artifacts
    was removed (analysis how_to_fix item 3): a stale canonical artifact
    from a previous run can no longer satisfy the current run's
    completion gate. The hardened contract requires either a current-run
    receipt at ``.agent/receipts/<run_id>/<type>.json`` (promoted from
    the agent's direct write) AND a completion sentinel via the real
    declare_complete MCP tool.
    """
    artifact_dir = tmp_path / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "development_result.md").write_text(
        "---\n"
        "type: development_result\n"
        "status: completed\n"
        "---\n\n"
        "## Summary\n\n- [SUM-1] done\n\n"
        "## Files Changed\n\n- [F-1] src/x.py\n",
        encoding="utf-8",
    )
    # The current-run receipt is what the AGY smoke plumbing now relies
    # on (the receipt is promoted from the agent's direct write via
    # ``promote_fallback_artifact``).
    run_id = "agy-on-disk-run-id"
    receipt_dir = tmp_path / ".agent" / "receipts" / run_id
    receipt_dir.mkdir(parents=True)
    (receipt_dir / "development_result.json").write_text(
        f'{{"run_id": "{run_id}", "artifact_type": "development_result"}}',
        encoding="utf-8",
    )

    strategy = strategy_for_transport(AgentTransport.AGY)
    handle = _FakeHandle(returncode=0)

    options = CompletionCheckOptions(
        execution_strategy=strategy,
        workspace_path=tmp_path,
        completion_run_id=run_id,
        required_artifact=RequiredArtifact(
            phase="development",
            artifact_type="development_result",
            artifact_path=".agent/artifacts/development_result.md",
            markdown_path=None,
            normalizer=None,
        ),
        policy=TimeoutPolicy(
            idle_timeout_seconds=None,
            parent_exit_grace_seconds=0.0,
        ),
    )

    with pytest.raises(AgentInvocationError):
        check_process_result(
            handle,
            "agy",
            [],
            options,
        )

    sentinel = tmp_path / ".agent" / f"completion_seen_{run_id}.json"
    sentinel.write_text(f'{{"run_id": "{run_id}"}}', encoding="utf-8")
    check_process_result(
        handle,
        "agy",
        [],
        options,
    )


def test_sentinel_check_fn_true_without_artifact_contract_prevents_invocation_error(
    tmp_path: Path,
) -> None:
    strategy = strategy_for_transport(AgentTransport.AGY)
    handle = _FakeHandle(returncode=0)

    check_process_result(
        handle,
        "agy",
        [],
        CompletionCheckOptions(
            execution_strategy=strategy,
            workspace_path=tmp_path,
            captured_session_id="captured-run-id",
            completion_run_id="run-sentinel-id",
            _sentinel_check_fn=lambda workspace, run_id: (
                workspace == tmp_path and run_id == "run-sentinel-id"
            ),
        ),
    )


def test_sentinel_check_fn_true_does_not_replace_required_receipt(
    tmp_path: Path,
) -> None:
    strategy = strategy_for_transport(AgentTransport.AGY)
    handle = _FakeHandle(returncode=0)

    with pytest.raises(AgentInvocationError):
        check_process_result(
            handle,
            "agy",
            [],
            CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                required_artifact=RequiredArtifact(
                    phase="development",
                    artifact_type="development_result",
                    artifact_path=".agent/artifacts/development_result.md",
                    markdown_path=None,
                    normalizer=None,
                ),
                captured_session_id="captured-run-id",
                completion_run_id="run-sentinel-id",
                _sentinel_check_fn=lambda _workspace, _run_id: True,
            ),
        )


def test_sentinel_check_fn_false_still_raises_invocation_error(tmp_path: Path) -> None:
    strategy = strategy_for_transport(AgentTransport.AGY)
    handle = _FakeHandle(returncode=0)

    with pytest.raises(AgentInvocationError):
        check_process_result(
            handle,
            "agy",
            [],
            CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                required_artifact=RequiredArtifact(
                    phase="development",
                    artifact_type="development_result",
                    artifact_path=".agent/artifacts/development_result.md",
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

    check_process_result(
        handle,
        "agy",
        [],
        CompletionCheckOptions(
            execution_strategy=strategy,
            workspace_path=tmp_path,
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

    check_process_result(
        handle,
        "agy",
        [],
        CompletionCheckOptions(
            execution_strategy=strategy,
            workspace_path=tmp_path,
            captured_session_id="parsed-session-001",
            completion_run_id="observable-run-001",
        ),
    )


def test_sentinel_absent_without_pty_echo_raises(tmp_path: Path) -> None:
    strategy = strategy_for_transport(AgentTransport.AGY)
    handle = _FakeHandle(returncode=0)

    with pytest.raises(AgentInvocationError):
        check_process_result(
            handle,
            "agy",
            [],
            CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                required_artifact=RequiredArtifact(
                    phase="development",
                    artifact_type="development_result",
                    artifact_path=".agent/artifacts/development_result.md",
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
    evidence = str(suspected[0].diagnostic.get("evidence", ""))
    assert "time_and_lifecycle_only" not in evidence
