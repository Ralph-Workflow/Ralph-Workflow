"""Authority audit — acceptance scenarios 5 and 6 (lifecycle + publication).

Acceptance scenario 5: complete a conflict resolution while leaving target
landing pending. The pipeline must continue the integration lifecycle
rather than re-enter development (no ``START_DEVELOPMENT`` which does not
exist in the closed PhaseDecision enum).

Acceptance scenario 6: complete local landing with remote publication
unavailable. The decision must reflect the configured publication policy
without confusing local and remote truth.

The publication state machine
(:mod:`ralph.pipeline._publication_state_machine`) is a pure function
that imports only the record type — it has NO evidenced dependency on
the loader or the recovery release fixes. These tests are the
``subprocess_e2e``-free regression pins the plan declares independent of
the loader/recovery close.
"""

from __future__ import annotations

from ralph.pipeline._pipeline_state_machine import (
    PhaseDecision,
    decide_phase_transition,
    is_terminal_success_eligible,
)
from ralph.pipeline._publication_state_machine import (
    PublicationDecision,
    next_publication_action,
)
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy


def _policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="development",
                ),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )


def _integrated_record() -> IntegrationRecord:
    return IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        integrated_feature_sha="c" * 40,
    )


def test_conflict_done_landing_pending_no_reentry_to_development() -> None:
    """Scenario 5: BLOCK_FOR_RECOVERY, not START_DEVELOPMENT (which does not exist)."""
    record = _integrated_record()
    rebase_state = RebaseState(fast_forwarded=False)
    state = PipelineState(phase="development", rebase=rebase_state)
    decision = decide_phase_transition(
        state,
        _policy(),
        integration_record=record,
        rebase_state=rebase_state,
    )
    # The PhaseDecision enum has no START_DEVELOPMENT value (closed set)
    assert decision is PhaseDecision.BLOCK_FOR_RECOVERY
    assert decision is not PhaseDecision.ADVANCE_PHASE
    assert decision is not PhaseDecision.CONTINUE_PHASE
    assert is_terminal_success_eligible(state, record, rebase_state) is False


def test_no_remote_configured_keeps_publication_terminal_with_local_landing_proof() -> None:
    """Scenario 6, no-remote branch: local landing is sufficient; no publication obligation."""
    decision = next_publication_action(
        RebaseState(fast_forwarded=True),
        record=_integrated_record(),
        remote_unavailable=False,
        configured_remote=None,
    )
    assert decision is PublicationDecision.PUBLICATION_TERMINAL
    # No publication obligation; once the record is cleared (terminal
    # success eligibility is checked after the record has been cleared
    # by the recovery seam), no authority blocks terminal success.
    rebase_state = RebaseState(fast_forwarded=True)
    state = PipelineState(phase="development", rebase=rebase_state)
    assert is_terminal_success_eligible(state, None, rebase_state) is True


def test_remote_configured_but_unavailable_keeps_deferred_publication_obligation() -> None:
    """Scenario 6, configured-but-unavailable branch: PUBLISH_DEFERRED_REMOTE."""
    decision = next_publication_action(
        RebaseState(fast_forwarded=True),
        record=_integrated_record(),
        remote_unavailable=True,
        configured_remote="origin",
    )
    # The decision is distinct from PUBLICATION_TERMINAL.
    assert decision is PublicationDecision.PUBLISH_DEFERRED_REMOTE
    assert decision is not PublicationDecision.PUBLICATION_TERMINAL
    # The publication obligation is not discharged; once the record is
    # cleared, the publication decision still records the deferred
    # obligation on the rebase state, so terminal success is ineligible.
    rebase_state = RebaseState(
        fast_forwarded=True,
        last_publication_decision=decision,
    )
    state = PipelineState(phase="development", rebase=rebase_state)
    assert is_terminal_success_eligible(state, None, rebase_state) is False
