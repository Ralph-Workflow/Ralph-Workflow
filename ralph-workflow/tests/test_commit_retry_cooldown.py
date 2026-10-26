from __future__ import annotations

from pathlib import Path

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.agents.invoke._direct_mcp_recovery import run_with_direct_mcp_recovery


def test_retry_budget_exhaustion_surfaces_failure_without_agent_choice_verdict() -> None:
    calls = 0
    failure = AgentInvocationError(
        "commit-agent",
        1,
        "Model returned an empty response with no tool calls",
        parsed_output=["invalid Files section"],
    )

    def run_attempt(_session_id: str | None, _capture_session_id: object) -> str:
        nonlocal calls
        calls += 1
        raise failure

    with pytest.raises(AgentInvocationError) as exc_info:
        run_with_direct_mcp_recovery(
            run_attempt,
            max_retries=2,
            reset_tool_registry=lambda: None,
        )

    assert calls == 3
    assert exc_info.value is failure
    diagnostic = "\n".join(exc_info.value.parsed_output)
    assert "retry budget exhausted after 3 attempts (2 retries)" in diagnostic
    assert "cause: AgentInvocationError" in diagnostic
    assert "identical failure signatures" not in diagnostic
    assert "try the next eligible agent" not in diagnostic


def test_commit_failure_records_failed_outcome_on_display_surface(tmp_path: Path) -> None:
    """AC11: A commit failure records 'failed' outcome on the display surface."""
    from rich.console import Console

    from ralph.display.context import make_display_context
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.commit_executor import execute_commit_effect, set_last_commit_error
    from ralph.pipeline.effects import CommitEffect
    from ralph.pipeline.events import PipelineEvent

    artifact_path = tmp_path / ".agent" / "artifacts" / "commit_message.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        "---\ntype: commit\nsubject: feat: test failing commit\n---\n",
        encoding="utf-8",
    )

    console = Console(record=True, force_terminal=False, width=120, color_system=None)
    display = ParallelDisplay(make_display_context(console=console, force_width=120))

    def failing_create_commit(
        _root: Path | str,
        _msg: str,
        *,
        expected_head: str,
    ) -> object:
        raise RuntimeError("Git create_commit failed: cannot lock ref")

    def _always_has_work(_repo: Path) -> bool:
        return True

    try:
        event = execute_commit_effect(
            CommitEffect(message_file=str(artifact_path)),
            tmp_path,
            display=display,
            create_commit_fn=failing_create_commit,
            has_commit_work_fn=_always_has_work,
        )

        assert event == PipelineEvent.COMMIT_FAILURE
        assert display.last_phase_artifact_outcome == "failed"
        assert display.last_phase_artifact_outcome != "completed"
    finally:
        set_last_commit_error(None)


def test_phase_display_honesty_on_commit_failure_never_reports_completed() -> None:
    """AC11: Commit failure clears pending completed outcome and routes to recovery, never failed_terminal."""
    from ralph.pipeline.commit_executor import set_last_commit_error
    from ralph.pipeline.events import PipelineEvent
    from ralph.pipeline.reducer import reduce
    from ralph.pipeline.state import PipelineState
    from ralph.policy.loader import load_policy

    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    policy = load_policy(defaults_dir).pipeline

    state = PipelineState(
        phase="development_commit",
        previous_phase="development_commit_cleanup",
        last_error="GitOperationError: cannot lock ref",
        pending_cycle_outcome="completed",
        last_execution_result_status="completed",
    )
    set_last_commit_error("GitOperationError: cannot lock ref")

    try:
        next_state, _effects = reduce(state, PipelineEvent.COMMIT_FAILURE, policy)

        assert next_state.phase != "failed_terminal"
        assert next_state.phase == "development_commit_cleanup"
        assert next_state.pending_cycle_outcome is None
        assert next_state.last_execution_result_status != "completed"
    finally:
        set_last_commit_error(None)


def test_complete_commit_rejects_missing_or_empty_sha() -> None:
    """AC11: Complete commit authority refuses to promote an integration record without a valid sha."""
    from ralph.pipeline._commit_completion_authority import complete_commit
    from ralph.pipeline.auto_integrate_record import IntegrationRecord

    record = IntegrationRecord(
        target="main",
        phase="integrating",
        pre_feature_sha="1" * 40,
        pre_target_sha="2" * 40,
    )

    with pytest.raises(
        ValueError,
        match="cannot promote integration record to integrated without a valid integrated_feature_sha",
    ):
        complete_commit(record, integrated_feature_sha=None)

    with pytest.raises(
        ValueError,
        match="cannot promote integration record to integrated without a valid integrated_feature_sha",
    ):
        complete_commit(record, integrated_feature_sha="   ")
