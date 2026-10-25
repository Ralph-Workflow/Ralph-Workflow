"""Single authority for pipeline progression decisions.

The run loop is the only caller of :func:`decide_phase_transition`,
which is the single authority for what runs next. The function is
pure: it takes its inputs by value and returns a typed
:class:`PhaseDecision`. The existing
:func:`ralph.pipeline.handoffs.resolve_next_phase` is consulted to
pick the next phase from the current state and a signal, but
``decide_phase_transition`` converts that into the typed
:class:`PhaseDecision` that the run loop, display, and prompt
materialisation read.

Decision ownership
------------------

The **integration rung** -- which integration mutation the boundary owes
next -- is decided by the single owner
:func:`ralph.pipeline.integration_owner.decide_integration`, reached through
the one seam :func:`ralph.pipeline._integration_owner_seam.decide_seam_action`.
This module asks the seam and acts on its verdict; it never re-derives the
rung from the durable record.

The **record facts the pure observation table cannot express** stay with
the seam:

* the typed fail-closed verdicts (:class:`~ralph.pipeline.integration_owner.
  RecordFactOutcome`) -- durable record facts (a spent diagnostic budget,
  a stale retained intent) routed to the autonomous fallback ladder,
  never to an operator hand-off.
* the completion / publication verdicts (``COMPLETE_WITH_PROOF``,
  ``PUBLISH_AFTER_LANDING``) -- derived from the record's landing proof
  and owned by :func:`ralph.pipeline._terminal_authority.is_terminal_success_eligible`.

The split is deliberate: the owner decides *what to do to the integration
state*, the record facts decide *whether the obligation is already
finished*.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
)
from ralph.pipeline._integration_owner_seam import decide_seam_action
from ralph.pipeline._terminal_authority import is_terminal_success_eligible
from ralph.pipeline.integration_director import (
    DEFAULT_REMOTE,
    DEFAULT_TARGET_BRANCH,
)
from ralph.pipeline.integration_owner import RecordFactOutcome

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PipelinePolicy


class PhaseDecision(StrEnum):
    """The closed vocabulary the run loop reads for progression.

    The run loop, the display formatter, the prompt materialiser,
    and the completion summary all consume the same enum values;
    no caller may infer progression from prose, the absence of a
    record, the cleanliness of a worktree, or the presence of a
    Git marker.
    """

    CONTINUE_PHASE = "continue_phase"
    ADVANCE_PHASE = "advance_phase"
    BLOCK_FOR_RECOVERY = "block_for_recovery"
    REPORT_TERMINAL_SUCCESS = "report_terminal_success"
    REPORT_TERMINAL_FAILURE = "report_terminal_failure"
    REPORT_INTERVENTION_REQUIRED = "report_intervention_required"


#: All known values of :class:`PhaseDecision`. ``Transition.ALL``
#: consults this for the closed-set invariant.
_ALL_DECISIONS: frozenset[str] = frozenset(d.value for d in PhaseDecision)

#: The owner's actions under which the integration obligation is still
#: open, so the pipeline must block for recovery instead of advancing.
#:
#: Every action the owner can project belongs here: the owner only ever
#: answers with a mutation the boundary still owes, a bounded wait, or a
#: landing. The record-fact completion / publication verdicts
#: (``COMPLETE_WITH_PROOF`` / ``PUBLISH_AFTER_LANDING``) are NOT owner
#: actions -- they are derived from the durable landing proof and are
#: owned by the terminal-success authority -- so they never block here.
_BLOCKING_ACTIONS: frozenset[IntegrationAction] = frozenset(
    {
        IntegrationAction.RESOLVE_ACTIVE_OPERATION,
        IntegrationAction.CONTINUE_GIT_OPERATION,
        IntegrationAction.REINTEGRATE_AGAINST_TARGET,
        IntegrationAction.LAND_TARGET,
        IntegrationAction.WAIT_FOR_BOUNDED_RETRY,
    }
)


def _configured_target(config: UnifiedConfig | None) -> str:
    """The configured integration target branch; ``main`` when unconfigured."""
    if config is None:
        return DEFAULT_TARGET_BRANCH
    return config.general.auto_integrate_target


def _configured_remote(config: UnifiedConfig | None) -> str:
    """The configured integration remote; ``origin`` when unconfigured."""
    if config is None:
        return DEFAULT_REMOTE
    return config.general.auto_integrate_remote


def _owner_action(
    root: Path | None,
    record: IntegrationRecord,
    *,
    config: UnifiedConfig | None,
    agent_active: bool,
) -> IntegrationAction | RecordFactOutcome:
    """Ask the single owner which integration rung the boundary owes next.

    ``root`` is the workspace root the owner observes and anchors the
    durable attempt ledger to. ``config`` supplies the CONFIGURED
    integration target and remote -- ``main`` and ``origin`` are defaults
    only, and the observation has to compare the configured refs. The
    boundary asks through the ONE seam, so the observation is built by
    the director (the one observation owner) and the durable record facts
    are overlaid on the owner's answer.

    Without a workspace root the seam cannot observe Git at all. It then
    falls back to the durable record fact: a retained record proves the
    integration obligation is still owed, which is the owner's repair
    rung.
    """
    if root is None:
        return IntegrationAction.RESOLVE_ACTIVE_OPERATION
    action = decide_seam_action(
        root,
        record=record,
        agent_active=agent_active,
        ledger_path=None,
        target=_configured_target(config),
        remote=_configured_remote(config),
    )
    return action


def _check_blocking_decision(
    integration_record: IntegrationRecord | None,
    rebase_state: RebaseState,
    *,
    root: Path | None,
    config: UnifiedConfig | None,
    agent_active: bool,
) -> PhaseDecision | None:
    """Derive the blocking decision from current authorities only.

    The blocking decision is a derived projection over current
    authority outputs, NOT a feedback input. ``rebase_state.last_decision``
    is the *persisted typed decision* from the previous run loop
    turn -- re-using it as a blocking input here would let a
    settled obligation remain blocked because the previous
    turn's decision was blocking. The current authority outputs
    (the owner verdict, the durable intervention flag) are the only
    inputs.
    """
    if integration_record is not None:
        # The durable record never carries the retired
        # ``diagnostic_intervention_required`` hand-off flag into this
        # decision: the loader load-and-clears legacy flagged payloads
        # into the autonomous fallback ladder. A spent diagnostic budget
        # surfaces as the typed record-fact outcome through the owner
        # seam (a blocking recovery action), never as a terminal
        # operator hand-off.
        action = _owner_action(
            root,
            integration_record,
            config=config,
            agent_active=agent_active,
        )
        if action in _BLOCKING_ACTIONS or isinstance(action, RecordFactOutcome):
            return PhaseDecision.BLOCK_FOR_RECOVERY
    if rebase_state.is_intervention_required is True:
        if rebase_state.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION:
            # The typed autonomous escalation (a spent diagnostic budget
            # or a stale retained intent) blocks for recovery: the
            # fallback ladder owns the next action. It is never the
            # terminal operator hand-off.
            return PhaseDecision.BLOCK_FOR_RECOVERY
        return PhaseDecision.REPORT_INTERVENTION_REQUIRED
    last_outcome = rebase_state.last_outcome
    fast_forwarded = rebase_state.fast_forwarded
    unlanded_rebase = (
        last_outcome in (RebaseOutcomeKind.REBASED, RebaseOutcomeKind.MERGED)
        and fast_forwarded is False
    )
    if (
        unlanded_rebase
        or rebase_state.integration_unresolved is True
        or rebase_state.recovery_record_retained is True
    ):
        return PhaseDecision.BLOCK_FOR_RECOVERY
    return None


def _is_terminal_failure(phase: str, policy: PipelinePolicy | None) -> bool:
    if policy is None:
        return False
    try:
        from ralph.pipeline.reducer import _terminal_failure_route, _terminal_success_route

        failure_route = _terminal_failure_route(policy)
        success_route = _terminal_success_route(policy)
        if failure_route != success_route:
            return phase == failure_route
        return False
    except Exception:
        return False


def decide_phase_transition(
    state: PipelineState,
    policy: PipelinePolicy | None = None,
    *,
    integration_record: IntegrationRecord | None,
    rebase_state: RebaseState,
    root: Path | None = None,
    config: UnifiedConfig | None = None,
    agent_active: bool = False,
) -> PhaseDecision:
    """Return the typed :class:`PhaseDecision` for the current state.

    The function is pure. It is the single authority for the
    question "what may the run loop do next?". The
    ``rebase_state.last_decision`` field is the single persisted
    surface for the typed decision; no caller may reconstruct the
    decision from prose or from auxiliary state.

    Args:
        state: The current pipeline state.
        policy: The pipeline policy; ``None`` means "no policy, keep
            going".
        integration_record: The durable integration record the caller
            holds, or ``None`` when it holds none.
        rebase_state: The current rebase state.
        root: The workspace root the single owner observes and anchors
            the durable attempt ledger to. ``None`` means the caller has
            no workspace to observe, and the seam decides from the
            durable record alone.
        config: The run configuration. It supplies the CONFIGURED
            integration target and remote the owner observes.
        agent_active: Whether an agent phase is active in the worktree.
            It is one of the observation dimensions the owner decides on.
    """
    blocking = _check_blocking_decision(
        integration_record,
        rebase_state,
        root=root,
        config=config,
        agent_active=agent_active,
    )
    if blocking is not None:
        return blocking
    if policy is None:
        return PhaseDecision.CONTINUE_PHASE

    terminal: set[str] = set()
    if hasattr(policy, "terminal_states"):
        terminal.update(policy.terminal_states())
    if hasattr(policy, "terminal_phase") and policy.terminal_phase:
        terminal.add(policy.terminal_phase)

    # Delegate to the existing reducer for the next-phase signal.
    from ralph.pipeline.handoffs import resolve_next_phase

    candidate_phase: str | None = None
    try:
        candidate_phase = resolve_next_phase(state.phase, "success", policy)
    except ValueError:
        if state.phase in terminal:
            candidate_phase = state.phase

    decision: PhaseDecision
    if candidate_phase is None:
        decision = PhaseDecision.REPORT_TERMINAL_FAILURE
    elif candidate_phase in terminal:
        if _is_terminal_failure(candidate_phase, policy):
            decision = PhaseDecision.REPORT_TERMINAL_FAILURE
        elif not is_terminal_success_eligible(state, integration_record, rebase_state):
            decision = PhaseDecision.BLOCK_FOR_RECOVERY
        else:
            decision = PhaseDecision.REPORT_TERMINAL_SUCCESS
    elif candidate_phase == state.phase:
        decision = PhaseDecision.CONTINUE_PHASE
    else:
        decision = PhaseDecision.ADVANCE_PHASE

    return decision


__all__ = [
    "PhaseDecision",
    "decide_phase_transition",
    "is_terminal_success_eligible",
]
