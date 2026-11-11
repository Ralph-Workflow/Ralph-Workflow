from __future__ import annotations

from ralph.pipeline.commit_executor import set_last_commit_error
from ralph.pipeline.commit_state import CommitState
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    RecoveryPolicy,
)

_APPROVED_TREE_SHA = "0123456789abcdef0123456789abcdef01234567"

_REPAIRABLE_REASON = "GitOperationError: Unable to create '.git/index.lock': File exists"


def _commit_failure_policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development_commit": PhaseDefinition(
                drain="development_commit",
                role="commit",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "development_final_commit": PhaseDefinition(
                drain="development_commit",
                role="commit",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "development_commit_review": PhaseDefinition(
                drain="development_commit_review",
                role="commit_review",
                transitions=PhaseTransition(on_success="development_commit"),
            ),
            "failed_terminal": PhaseDefinition(
                drain="failed_terminal",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        entry_phase="development_commit",
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )


def test_commit_failure_ignores_the_diagnostic_of_an_earlier_attempt() -> None:
    set_last_commit_error(_REPAIRABLE_REASON)
    state = PipelineState(
        phase="development_final_commit",
        last_error="Artifact validation fault: stale proof mismatch",
    )

    new_state, effects = reduce(state, PipelineEvent.COMMIT_FAILURE, _commit_failure_policy())

    assert new_state.phase == "failed_terminal"
    assert new_state.last_error == "development_final_commit: Commit failed"
    assert effects == []


def test_commit_failure_ignores_a_diagnostic_bound_to_another_approval() -> None:
    set_last_commit_error(_REPAIRABLE_REASON)
    state = PipelineState(
        phase="development_commit",
        approved_tree_sha=_APPROVED_TREE_SHA,
        commit=CommitState(agent_invoked=True),
    )

    new_state, effects = reduce(state, PipelineEvent.COMMIT_FAILURE, _commit_failure_policy())

    assert new_state.phase == "failed_terminal"
    assert new_state.last_error == "development_commit: Commit failed"
    assert effects == []


def test_commit_failure_uses_the_diagnostic_of_its_own_attempt() -> None:
    set_last_commit_error(_REPAIRABLE_REASON)
    state = PipelineState(
        phase="development_commit",
        commit=CommitState(agent_invoked=True),
    )

    new_state, effects = reduce(state, PipelineEvent.COMMIT_FAILURE, _commit_failure_policy())

    assert new_state.phase == "development_commit_review"
    assert new_state.last_error == _REPAIRABLE_REASON
    assert effects == []
