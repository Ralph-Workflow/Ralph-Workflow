from __future__ import annotations

from pathlib import Path

import pytest

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.auto_integrate_outcome import record_resolution_outcome
from ralph.pipeline.conflict_resolution import ResolutionTerminationReason
from ralph.pipeline.resolution_outcome_config_failure import ConfigFailure
from ralph.pipeline.resolution_outcome_exhausted import Exhausted
from ralph.pipeline.resolution_outcome_needs_operator import NeedsOperator
from ralph.pipeline.resolution_outcome_producer import classify_resolution_failure
from ralph.pipeline.resolution_outcome_resolved import Resolved
from ralph.pipeline.resolution_outcome_retry_later import RetryLater
from ralph.pipeline.resolution_outcome_type import ResolutionOutcomeType
from ralph.workspace.errors import WorkspaceContextError


def test_config_failure_uses_typed_file_and_parse_error(tmp_path: Path) -> None:
    config_file = tmp_path / ".agent" / "ralph-workflow.toml"
    parse_error = ValueError("invalid TOML")
    error = WorkspaceContextError(config_file, "invalid TOML")
    error.__cause__ = parse_error

    outcome = classify_resolution_failure(
        terminal_reason=ResolutionTerminationReason.CONFIG_FAILURE,
        failure_cause=ResolutionFailureCause.CONFIG_FAILURE,
        reason=f"fix {config_file} then run ralph --check-config",
        config_error=error,
    )

    match outcome:
        case ConfigFailure(file=file, parse_error=error_payload):
            assert file == config_file
            assert error_payload is parse_error
        case _ as unreachable:
            raise AssertionError(f"expected ConfigFailure, got {unreachable!r}")


def test_config_text_alone_does_not_create_config_failure(tmp_path: Path) -> None:
    config_file = tmp_path / ".agent" / "ralph-workflow.toml"
    outcome = classify_resolution_failure(
        terminal_reason=ResolutionTerminationReason.CONFIG_FAILURE,
        failure_cause=ResolutionFailureCause.CONFIG_FAILURE,
        reason=f"fix {config_file} then run ralph --check-config",
    )

    match outcome:
        case RetryLater(on_change=on_change):
            assert on_change == f"fix {config_file} then run ralph --check-config"
        case _ as unreachable:
            raise AssertionError(f"expected RetryLater, got {unreachable!r}")


def test_spent_chain_is_exhausted() -> None:
    outcome = classify_resolution_failure(
        terminal_reason=ResolutionTerminationReason.CANDIDATE_EXHAUSTED,
        failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
        paths=("app.py",),
        agents=("primary", "fallback"),
    )

    match outcome:
        case Exhausted(agents=agents):
            assert agents == ("primary", "fallback")
        case _ as unreachable:
            raise AssertionError(f"expected Exhausted, got {unreachable!r}")
    state = record_resolution_outcome(outcome, target="main")
    assert state.resolution_exhausted is True
    assert "primary, fallback" in (state.last_reason or "")


def test_out_of_reach_is_needs_operator() -> None:
    outcome = classify_resolution_failure(
        terminal_reason=ResolutionTerminationReason.OUT_OF_REACH,
        failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
        paths=("vendor/sub",),
    )

    match outcome:
        case NeedsOperator(cause=cause, paths=paths, command=command):
            assert cause is ResolutionFailureCause.UNRESOLVED_CONFLICT
            assert paths == ("vendor/sub",)
            assert command == "git add -- vendor/sub"
        case _ as unreachable:
            raise AssertionError(f"expected NeedsOperator, got {unreachable!r}")


def test_exhausted_but_not_spent_is_retry_later() -> None:
    outcome = classify_resolution_failure(
        terminal_reason=ResolutionTerminationReason.TOOL_SURFACE_DEAD,
        failure_cause=ResolutionFailureCause.AGENT_FAILURE,
    )

    match outcome:
        case RetryLater(on_change=on_change):
            assert on_change is not None
        case _ as unreachable:
            raise AssertionError(f"expected RetryLater, got {unreachable!r}")


def test_union_is_closed_and_consumer_rejects_unknown_variant() -> None:
    assert set(ResolutionOutcomeType.__value__.__args__) == {
        Resolved,
        NeedsOperator,
        RetryLater,
        Exhausted,
        ConfigFailure,
    }
    unknown: object = "unhandled"
    with pytest.raises(AssertionError, match="Expected code to be unreachable"):
        record_resolution_outcome(unknown, target="main")
