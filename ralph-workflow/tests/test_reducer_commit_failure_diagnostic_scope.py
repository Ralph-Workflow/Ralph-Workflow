"""Regression: a commit failure must not route with another attempt's diagnostic.

The commit executor hands the reducer the diagnostic of the commit attempt that
failed. That handoff used to be an unscoped process-global, so a diagnostic
left behind by an earlier attempt -- one whose failure was never routed -- was
read as the diagnostic of the failure being routed. The stale prose matched the
reducer's repairable-git-failure patterns, so the commit failure was re-routed
as a repairable git failure (an in-place retry) instead of escalating to the
policy's terminal failure route, and the outcome depended on whichever commit
attempt happened to fail last in the process.

These tests pin the two properties that keep the handoff scoped: the pending
diagnostic is bound to the approval of the attempt that produced it, and the
state must corroborate the diagnostic before it may route the failure.
"""

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

#: The approval a state carries after a commit review approved a tree.
_APPROVED_TREE_SHA = "0123456789abcdef0123456789abcdef01234567"

#: A repairable git diagnostic: the prose the reducer routes as a git failure.
_REPAIRABLE_REASON = "GitOperationError: Unable to create '.git/index.lock': File exists"


def _commit_failure_policy() -> PipelinePolicy:
    """Return a policy with a commit-review recovery phase and a failure route."""
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
    """A diagnostic left behind by another attempt must not route this failure.

    The earlier attempt's failure was never routed, so its diagnostic is still
    pending when an unrelated commit failure arrives. This state recorded a
    DIFFERENT failure and never invoked a commit attempt, so it has no claim on
    that diagnostic, and the failure is routed from the canonical reason.
    """
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
    """A diagnostic of an attempt made under another approval must not route this failure."""
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
    """The diagnostic of the attempt being routed still drives the recovery route."""
    set_last_commit_error(_REPAIRABLE_REASON)
    state = PipelineState(
        phase="development_commit",
        commit=CommitState(agent_invoked=True),
    )

    new_state, effects = reduce(state, PipelineEvent.COMMIT_FAILURE, _commit_failure_policy())

    assert new_state.phase == "development_commit_review"
    assert new_state.last_error == _REPAIRABLE_REASON
    assert effects == []
