from __future__ import annotations

from pathlib import Path

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.auto_integrate_resolve import (
    MERGE_COMMIT_PENDING,
    handle_unsuccessful_resolution,
)
from ralph.pipeline.resolution_outcome import (
    ConfigFailureOutcome,
    NextStrategyOutcome,
    RetryLaterOutcome,
    UnknownOutcome,
    WaitingOutcome,
)


def test_exhausted_chain_advances_to_the_named_autonomous_strategy() -> None:
    result = handle_unsuccessful_resolution(
        NextStrategyOutcome("completion_in_place"),
        reason=None,
        conflicted=("conflict_a.py", "conflict_b.py"),
    )

    assert result.outcome == MERGE_COMMIT_PENDING
    assert result.failure_cause is ResolutionFailureCause.UNRESOLVED_CONFLICT
    assert result.reason == "continuing with autonomous strategy: completion_in_place"


def test_retry_later_retains_progress_until_the_observed_condition_changes() -> None:
    result = handle_unsuccessful_resolution(
        RetryLaterOutcome(on_change="eligible resolver changes"),
        reason=None,
        conflicted=("file1.txt",),
    )

    assert result.outcome == MERGE_COMMIT_PENDING
    assert result.failure_cause is ResolutionFailureCause.AGENT_FAILURE
    assert result.reason == "eligible resolver changes"


def test_waiting_outcome_names_the_external_resume_condition() -> None:
    result = handle_unsuccessful_resolution(
        WaitingOutcome("provider quota", "provider quota resets"),
        reason=None,
    )

    assert result.outcome == MERGE_COMMIT_PENDING
    assert result.failure_cause is ResolutionFailureCause.CONTEXT_FAILURE
    assert result.reason == "waiting for provider quota; resumes when provider quota resets"


def test_configuration_failure_names_the_file_and_operator_command() -> None:
    config = Path("/workspace/.agent/ralph-workflow.toml")

    result = handle_unsuccessful_resolution(
        ConfigFailureOutcome(config, "invalid table"),
        reason=None,
    )

    assert result.outcome == MERGE_COMMIT_PENDING
    assert result.failure_cause is ResolutionFailureCause.CONFIG_FAILURE
    assert result.reason == f"fix {config} then run ralph --check-config"


def test_unknown_failure_never_claims_resolution_success() -> None:
    result = handle_unsuccessful_resolution(
        UnknownOutcome(RuntimeError("provider unavailable")),
        reason=None,
    )

    assert result.outcome == MERGE_COMMIT_PENDING
    assert result.failure_cause is ResolutionFailureCause.CONTEXT_FAILURE
    assert "provider unavailable" in (result.reason or "")
