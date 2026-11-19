from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline import resolve_conflicted_rebase
from ralph.pipeline.auto_integrate_outcome import record_resolution_exhausted
from ralph.pipeline.conflict_resolution import ResolutionTerminationReason
from ralph.pipeline.rebase_resolution_result import RebaseResolutionResult
from ralph.pipeline.resolution_outcome import RetryLaterOutcome

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_resolve_rebase_with_config_exposes_no_runnable_candidate_terminal_reason (renamed)": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_resolve_rebase_with_config_exposes_candidate_exhausted_terminal_reason with an unchanged body"
    ),
    "test_rebase_resolution_result_keeps_typed_contract": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; the check is unchanged and now reads the renamed cooldown vocabulary (benches_agent -> starts_cooldown, verdict_benches_agent -> verdict_starts_cooldown, false_bench_* -> false_cooldown_*, CANDIDATE_EXHAUSTED -> NO_RUNNABLE_CANDIDATE, exhaustion_reason -> halt_reason, 'spent/unavailable' -> 'on cooldown')"
    ),
    "test_record_resolution_exhausted_records_the_terminal_reason": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; the check is unchanged and now reads the renamed cooldown vocabulary (benches_agent -> starts_cooldown, verdict_benches_agent -> verdict_starts_cooldown, false_bench_* -> false_cooldown_*, CANDIDATE_EXHAUSTED -> NO_RUNNABLE_CANDIDATE, exhaustion_reason -> halt_reason, 'spent/unavailable' -> 'on cooldown')"
    ),
    "test_resolve_rebase_with_config_exposes_no_runnable_candidate_terminal_reason": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; the check is unchanged and now reads the renamed cooldown vocabulary (benches_agent -> starts_cooldown, verdict_benches_agent -> verdict_starts_cooldown, false_bench_* -> false_cooldown_*, CANDIDATE_EXHAUSTED -> NO_RUNNABLE_CANDIDATE, exhaustion_reason -> halt_reason, 'spent/unavailable' -> 'on cooldown')"
    ),
    "test_resolve_rebase_with_config_result_unpacks_into_three_values": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; the check is unchanged and now reads the renamed cooldown vocabulary (benches_agent -> starts_cooldown, verdict_benches_agent -> verdict_starts_cooldown, false_bench_* -> false_cooldown_*, CANDIDATE_EXHAUSTED -> NO_RUNNABLE_CANDIDATE, exhaustion_reason -> halt_reason, 'spent/unavailable' -> 'on cooldown')"
    ),
}


pytestmark = pytest.mark.timeout_seconds(5)

_PENDING_REASON = "rebase resolution remains pending after continuation"
_OPERATOR_ACTION_MESSAGE = (
    "all resolver agents are on cooldown; conflict on app.py; resume point: git add -- app.py"
)


class _NoRunnableCandidateRunner:
    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        *,
        session: ResolutionSession,
    ) -> bool:
        session.terminal_reason = ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE
        session.unresolved_paths = ("app.py",)
        session.operator_action_message = _OPERATOR_ACTION_MESSAGE
        return False


class _ResolvedRunner:
    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        *,
        session: ResolutionSession,
    ) -> bool:
        return True


def test_resolve_rebase_with_config_exposes_no_runnable_candidate_terminal_reason(
    tmp_path: Path,
) -> None:
    result = resolve_conflicted_rebase(
        tmp_path,
        "main",
        lambda *_args: False,
        None,
        None,
        agent_runner=_NoRunnableCandidateRunner(),
    )

    assert result is not None
    assert result.short_circuit is not None
    assert result.short_circuit.resolution_exhaustion_reason is not None
    assert result.short_circuit.resolution_exhaustion_reason.startswith(
        ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE.value
    )


def test_resolve_rebase_with_config_exposes_no_terminal_reason_when_resolved(
    tmp_path: Path,
) -> None:
    result = resolve_conflicted_rebase(
        tmp_path,
        "main",
        lambda *_args: False,
        None,
        None,
        agent_runner=_ResolvedRunner(),
    )

    assert result is not None
    assert result.short_circuit is None


def test_resolve_rebase_with_config_result_unpacks_into_three_values(tmp_path: Path) -> None:
    public_result = resolve_conflicted_rebase(
        tmp_path,
        "main",
        lambda *_args: False,
        None,
        None,
        agent_runner=_NoRunnableCandidateRunner(),
    )
    assert public_result is not None
    assert public_result.short_circuit is not None
    result = RebaseResolutionResult(
        RetryLaterOutcome(
            on_change=_OPERATOR_ACTION_MESSAGE,
            terminal_reason=ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE,
            failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
        ),
        _OPERATOR_ACTION_MESSAGE,
        ResolutionFailureCause.UNRESOLVED_CONFLICT,
        ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE,
    )

    assert isinstance(result.outcome, RetryLaterOutcome)
    assert result.reason == _OPERATOR_ACTION_MESSAGE
    assert result.failure_cause is ResolutionFailureCause.UNRESOLVED_CONFLICT
    assert result.terminal_reason is ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE


def test_rebase_resolution_result_keeps_typed_contract() -> None:
    positional = RebaseResolutionResult(
        RetryLaterOutcome(on_change=_PENDING_REASON),
        _PENDING_REASON,
        ResolutionFailureCause.UNRESOLVED_CONFLICT,
        ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE,
    )
    defaulted = RebaseResolutionResult(
        RetryLaterOutcome(on_change=_PENDING_REASON),
        _PENDING_REASON,
    )

    for result in (positional, defaulted):
        assert isinstance(result.outcome, RetryLaterOutcome)
        assert result.reason == _PENDING_REASON
        assert result.outcome.on_change == _PENDING_REASON

    assert defaulted.terminal_reason is None
    assert positional.terminal_reason is ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE
    assert positional == RebaseResolutionResult(
        RetryLaterOutcome(on_change=_PENDING_REASON),
        _PENDING_REASON,
        ResolutionFailureCause.UNRESOLVED_CONFLICT,
        ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE,
    )


def test_record_resolution_exhausted_records_the_terminal_reason() -> None:
    state = record_resolution_exhausted(
        reason="all resolver agents are on cooldown",
        target="main",
        terminal_reason=ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE,
    )

    assert state.resolution_exhausted is True
    assert state.last_reason == "all resolver agents are on cooldown"
    assert state.resolution_exhaustion_reason is not None
    assert state.resolution_exhaustion_reason.startswith(
        ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE.value
    )
    assert "all resolver agents are on cooldown" in state.resolution_exhaustion_reason


def test_record_resolution_exhausted_without_terminal_reason_keeps_the_reason() -> None:
    state = record_resolution_exhausted(reason="exhausted", target="main")

    assert state.resolution_exhausted is True
    assert state.resolution_exhaustion_reason == "exhausted"
