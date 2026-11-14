from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline import resolve_conflicted_rebase
from ralph.pipeline.auto_integrate_outcome import record_resolution_exhausted
from ralph.pipeline.conflict_resolution import ResolutionTerminationReason
from ralph.pipeline.rebase_resolution_result import RebaseResolutionResult

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

pytestmark = pytest.mark.timeout_seconds(5)

_PENDING_REASON = "rebase resolution remains pending after continuation"
_OPERATOR_ACTION_MESSAGE = (
    "all resolver agents are spent/unavailable; "
    "conflict on app.py needs operator action: git add -- app.py"
)


class _CandidateExhaustedRunner:
    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        *,
        session: ResolutionSession,
    ) -> bool:
        session.terminal_reason = ResolutionTerminationReason.CANDIDATE_EXHAUSTED
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


def test_resolve_rebase_with_config_exposes_candidate_exhausted_terminal_reason(
    tmp_path: Path,
) -> None:
    result = resolve_conflicted_rebase(
        tmp_path,
        "main",
        lambda *_args: False,
        None,
        None,
        agent_runner=_CandidateExhaustedRunner(),
    )

    assert result is not None
    assert result.short_circuit is not None
    assert result.short_circuit.resolution_exhaustion_reason is not None
    assert result.short_circuit.resolution_exhaustion_reason.startswith(
        ResolutionTerminationReason.CANDIDATE_EXHAUSTED.value
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
        agent_runner=_CandidateExhaustedRunner(),
    )
    assert public_result is not None
    assert public_result.short_circuit is not None
    result = RebaseResolutionResult(
        False,
        public_result.short_circuit.last_reason,
        public_result.short_circuit.last_failure_cause,
    )

    resolved, reason, failure_cause = result
    assert resolved is False
    assert reason == _OPERATOR_ACTION_MESSAGE
    assert failure_cause is ResolutionFailureCause.UNRESOLVED_CONFLICT
    assert len(result) == 3
    assert list(result) == [
        False,
        _OPERATOR_ACTION_MESSAGE,
        ResolutionFailureCause.UNRESOLVED_CONFLICT,
    ]


def test_rebase_resolution_result_keeps_three_value_contract_for_existing_call_sites() -> None:
    positional = RebaseResolutionResult(
        False,
        _PENDING_REASON,
        ResolutionFailureCause.UNRESOLVED_CONFLICT,
        ResolutionTerminationReason.CANDIDATE_EXHAUSTED,
    )
    keyword = RebaseResolutionResult(
        succeeded=False,
        reason=_PENDING_REASON,
        failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
    )
    defaulted = RebaseResolutionResult(False, _PENDING_REASON)

    for result, expected_cause in (
        (positional, ResolutionFailureCause.UNRESOLVED_CONFLICT),
        (keyword, ResolutionFailureCause.UNRESOLVED_CONFLICT),
        (defaulted, ResolutionFailureCause.NONE),
    ):
        succeeded, reason, failure_cause = result
        assert succeeded is False
        assert reason == _PENDING_REASON
        assert failure_cause is expected_cause
        assert len(result) == 3
        assert tuple(result) == (False, _PENDING_REASON, expected_cause)

    assert defaulted.terminal_reason is None
    assert positional.terminal_reason is ResolutionTerminationReason.CANDIDATE_EXHAUSTED
    assert positional == (
        False,
        _PENDING_REASON,
        ResolutionFailureCause.UNRESOLVED_CONFLICT,
    )


def test_record_resolution_exhausted_records_the_terminal_reason() -> None:
    state = record_resolution_exhausted(
        reason="all resolver agents are spent/unavailable",
        target="main",
        terminal_reason=ResolutionTerminationReason.CANDIDATE_EXHAUSTED,
    )

    assert state.resolution_exhausted is True
    assert state.last_reason == "all resolver agents are spent/unavailable"
    assert state.resolution_exhaustion_reason is not None
    assert state.resolution_exhaustion_reason.startswith(
        ResolutionTerminationReason.CANDIDATE_EXHAUSTED.value
    )
    assert "all resolver agents are spent/unavailable" in state.resolution_exhaustion_reason


def test_record_resolution_exhausted_without_terminal_reason_keeps_the_reason() -> None:
    state = record_resolution_exhausted(reason="exhausted", target="main")

    assert state.resolution_exhausted is True
    assert state.resolution_exhaustion_reason == "exhausted"
