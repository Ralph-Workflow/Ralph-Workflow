from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.auto_integrate_outcome import record_resolution_outcome
from ralph.pipeline.conflict_resolution import ResolutionTerminationReason
from ralph.pipeline.resolution_outcome import (
    ConfigFailureOutcome,
    NextStrategyOutcome,
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
    UnknownOutcome,
    WaitingOutcome,
)
from ralph.pipeline.resolution_outcome_producer import classify_resolution_failure
from ralph.pipeline.resolution_outcome_type import ResolutionOutcomeType
from ralph.workspace.errors import WorkspaceContextError

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_config_failure_uses_typed_file_and_parse_error": "migrate assertion from retired ConfigFailure to canonical ConfigFailureOutcome",
    "test_config_text_alone_does_not_create_config_failure": "migrate assertion from retired RetryLater to canonical RetryLaterOutcome",
    "test_spent_chain_defers_until_resolver_eligibility_changes": "verify spent chain defers with RetryLaterOutcome on resolver_agent_eligibility_changed without marking resolution_exhausted",
    "test_out_of_reach_is_needs_operator": "verify out-of-reach classification returns canonical NextStrategyOutcome with completion_in_place strategy",
    "test_out_of_reach_advances_to_completion_in_place": "verify out-of-reach classification returns canonical NextStrategyOutcome with completion_in_place strategy",
    "test_exhausted_but_not_spent_is_retry_later": "migrate assertion from retired RetryLater to canonical RetryLaterOutcome",
    "test_candidate_exhausted_defers_without_leaking_detail": "pin the exhausted-candidate deferral signal so agent and operator detail never replaces resolver_agent_eligibility_changed",
    "test_union_is_closed_and_consumer_rejects_unknown_variant": "assert ResolutionOutcomeType union matches canonical closed variants including UnknownOutcome",
}


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
        case ConfigFailureOutcome(file=file, parse_error=error_payload):
            assert file == config_file
            assert error_payload is parse_error
        case _ as unreachable:
            raise AssertionError(f"expected ConfigFailureOutcome, got {unreachable!r}")


def test_config_text_alone_does_not_create_config_failure(tmp_path: Path) -> None:
    config_file = tmp_path / ".agent" / "ralph-workflow.toml"
    outcome = classify_resolution_failure(
        terminal_reason=ResolutionTerminationReason.CONFIG_FAILURE,
        failure_cause=ResolutionFailureCause.CONFIG_FAILURE,
        reason=f"fix {config_file} then run ralph --check-config",
    )

    match outcome:
        case RetryLaterOutcome(on_change=on_change):
            assert on_change == f"fix {config_file} then run ralph --check-config"
        case _ as unreachable:
            raise AssertionError(f"expected RetryLaterOutcome, got {unreachable!r}")


def test_spent_chain_defers_until_resolver_eligibility_changes() -> None:
    outcome = classify_resolution_failure(
        terminal_reason=ResolutionTerminationReason.CANDIDATE_EXHAUSTED,
        failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
        paths=("app.py",),
        agents=("primary", "fallback"),
    )

    match outcome:
        case RetryLaterOutcome(on_change=on_change):
            assert on_change == "resolver_agent_eligibility_changed"
        case _ as unreachable:
            raise AssertionError(f"expected RetryLaterOutcome, got {unreachable!r}")
    state = record_resolution_outcome(outcome, target="main")
    assert state.resolution_exhausted is False
    assert state.last_reason == "resolver_agent_eligibility_changed"


def test_candidate_exhausted_defers_without_leaking_detail() -> None:
    outcome = classify_resolution_failure(
        terminal_reason=ResolutionTerminationReason.CANDIDATE_EXHAUSTED,
        failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
        reason="all resolver agents are spent/unavailable; conflict on app.py needs operator action",
        paths=("app.py",),
        agents=("primary", "fallback"),
    )

    match outcome:
        case RetryLaterOutcome(on_change=on_change):
            assert on_change == "resolver_agent_eligibility_changed"
        case _ as unreachable:
            raise AssertionError(f"expected RetryLaterOutcome, got {unreachable!r}")
    state = record_resolution_outcome(outcome, target="main")
    assert state.resolution_exhausted is False
    assert state.last_reason == "resolver_agent_eligibility_changed"


def test_out_of_reach_advances_to_completion_in_place() -> None:
    outcome = classify_resolution_failure(
        terminal_reason=ResolutionTerminationReason.OUT_OF_REACH,
        failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
        paths=("vendor/sub",),
    )

    match outcome:
        case NextStrategyOutcome(strategy=strategy):
            assert strategy == "completion_in_place"
        case _ as unreachable:
            raise AssertionError(f"expected NextStrategyOutcome, got {unreachable!r}")
    state = record_resolution_outcome(outcome, target="main")
    assert state.last_failure_cause is ResolutionFailureCause.UNRESOLVED_CONFLICT
    assert state.last_reason == "continuing with autonomous strategy: completion_in_place"


def test_exhausted_but_not_spent_is_retry_later() -> None:
    outcome = classify_resolution_failure(
        terminal_reason=ResolutionTerminationReason.TOOL_SURFACE_DEAD,
        failure_cause=ResolutionFailureCause.AGENT_FAILURE,
    )

    match outcome:
        case RetryLaterOutcome(on_change=on_change):
            assert on_change is not None
        case _ as unreachable:
            raise AssertionError(f"expected RetryLaterOutcome, got {unreachable!r}")


def test_union_is_closed_and_consumer_rejects_unknown_variant() -> None:
    assert set(ResolutionOutcomeType.__value__.__args__) == {
        ResolvedOutcome,
        WaitingOutcome,
        RetryLaterOutcome,
        NextStrategyOutcome,
        ConfigFailureOutcome,
        UnknownOutcome,
    }
    assert set(ResolutionOutcomeType.__value__.__args__) == set(
        StopResolutionOutcome.__value__.__args__
    )
    unknown: object = "unhandled"

    with pytest.raises(AssertionError, match="Expected code to be unreachable"):
        record_resolution_outcome(cast("StopResolutionOutcome", unknown), target="main")
