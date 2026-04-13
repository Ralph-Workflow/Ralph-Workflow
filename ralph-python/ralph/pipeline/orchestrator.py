"""Pure orchestrator: determine next effect from state and policy.

This module implements the core routing logic that was previously hardcoded
in the reducer's match arms. Given the current PipelineState and the loaded
PipelinePolicy + AgentsPolicy, determine_next_effect() returns the next
Effect to execute.

No I/O, no side effects — fully deterministic and testable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.config.enums import (
    PHASE_COMPLETE,
    PHASE_DEVELOPMENT,
    PHASE_FAILED,
    PHASE_REVIEW,
    PipelinePhase,
)
from ralph.pipeline.effects import (
    Effect,
    ExitFailureEffect,
    ExitSuccessEffect,
    InvokeAgentEffect,
    PreparePromptEffect,
)

if TYPE_CHECKING:
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import (
        AgentChainConfig,
        AgentsPolicy,
        PhaseDefinition,
        PipelinePolicy,
    )


class PhaseHandlerNotFoundError(Exception):
    """Raised when no handler is registered for a phase.

    Attributes:
        phase: Phase name that has no handler.
    """

    def __init__(self, phase: str) -> None:
        self.phase = phase
        msg = (
            f"No handler registered for phase '{phase}'. "
            f"To use custom phase names, register a handler in ralph/phases/__init__.py"
        )
        super().__init__(msg)


def determine_next_effect(
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
    agents_policy: AgentsPolicy,
) -> Effect:
    """Pure function: derive next effect from current state and policy.

    This is the single routing function that replaces all hardcoded phase
    routing in the reducer. It consults the pipeline policy to determine
    which effect to emit based on:
    - The current phase definition (drain, transitions)
    - State flags (prompt prepared, agent invoked, analysis complete)
    - Budget counters (development_budget_remaining, review_budget_remaining)

    Args:
        state: Current pipeline state.
        pipeline_policy: Loaded pipeline policy (phase graph from pipeline.toml).
        agents_policy: Loaded agents policy (chains and drain bindings).

    Returns:
        The next Effect to execute.
    """
    # Terminal states always produce exit effects
    if state.phase == PHASE_COMPLETE:
        return ExitSuccessEffect()

    if state.phase == PHASE_FAILED:
        return ExitFailureEffect(reason=state.last_error or "Unknown failure")

    # Look up the current phase definition
    phase_def = pipeline_policy.phases.get(state.phase)
    if phase_def is None:
        return _handle_unknown_phase(state)

    drain = phase_def.drain
    chain_name = agents_policy.agent_drains[drain].chain
    chain = agents_policy.agent_chains[chain_name]

    # Routing based on phase type and state flags
    return _derive_effect_for_phase(state, phase_def, chain, chain_name)


def _derive_effect_for_phase(
    state: PipelineState,
    phase_def: PhaseDefinition,
    chain: AgentChainConfig,
    chain_name: str,
) -> Effect:
    """Derive the next effect for a known phase.

    Args:
        state: Current pipeline state.
        phase_def: Phase definition from pipeline policy.
        chain: Agent chain config for the current drain.
        chain_name: Name of the agent chain.

    Returns:
        Next Effect to execute.
    """
    phase = state.phase

    # Check if we need to invoke the agent or prepare the prompt first
    if not _is_agent_invoked_for_phase(state, phase):
        # First time in this phase — prepare prompt then invoke agent
        return PreparePromptEffect(phase=phase, iteration=state.iteration)

    # Agent was already invoked for this phase — check for analysis routing
    if phase_def.embeds_analysis:
        # Analysis routing is handled via events, not here
        # The orchestrator returns InvokeAgentEffect when analysis is pending
        pass

    # Check budget for commit-gated phases
    if phase_def.requires_commit and _commit_budget_exhausted(state, phase):
        # Budget exhausted — advance to next phase
        return _route_transition(state, phase_def, "on_success")

    return InvokeAgentEffect(
        agent_name=_current_agent_name(state, chain),
        phase=phase,
        prompt_file="PROMPT.md",
        chain_name=chain_name,
    )


def _is_agent_invoked_for_phase(state: PipelineState, phase: PipelinePhase) -> bool:
    """Check whether the agent has been invoked for the given phase.

    This is a state-based check. In the current design, once an agent
    is invoked for a phase, a corresponding flag is set in the state.

    Args:
        state: Current pipeline state.
        phase: Phase to check.

    Returns:
        True if the agent for this phase has been invoked.
    """
    # For phases with commit state, check the commit state
    if phase in ("development_commit", "review_commit"):
        return state.commit.agent_invoked

    # For review phases, check review_issues_found
    if phase == PHASE_REVIEW:
        # Review "clean" means the phase ran but found no issues
        # The actual logic is in the event handler
        pass

    # Default: no invocation tracked, always invoke
    return False


def _commit_budget_exhausted(state: PipelineState, phase: PipelinePhase) -> bool:
    """Check if the commit budget is exhausted for a commit-gated phase.

    Args:
        state: Current pipeline state.
        phase: Commit-gated phase name.

    Returns:
        True if budget is exhausted and we should advance.
    """
    if phase == "development_commit":
        return state.development_budget_remaining <= 0

    if phase == "review_commit":
        return state.review_budget_remaining <= 0

    return False


def _current_agent_name(
    state: PipelineState,
    chain: AgentChainConfig,
) -> str:
    """Get the current agent name from the chain.

    Args:
        state: Current pipeline state.
        chain: Agent chain config.

    Returns:
        Name of the current agent to invoke.
    """
    # For phases with dev_chain/rev_chain
    if state.phase == PHASE_DEVELOPMENT:
        agents = state.dev_chain.agents
        idx = state.dev_chain.current_index
    elif state.phase == PHASE_REVIEW:
        agents = state.rev_chain.agents
        idx = state.rev_chain.current_index
    else:
        # Fallback to first agent in chain
        return chain.agents[0]

    if idx < len(agents):
        return agents[idx]
    return chain.agents[0]


def _route_transition(
    state: PipelineState,
    phase_def: PhaseDefinition,
    transition_key: str,
) -> Effect:
    """Route to the phase specified by a transition.

    Args:
        state: Current pipeline state.
        phase_def: Current phase definition.
        transition_key: Which transition to follow (on_success, on_failure, on_loopback).

    Returns:
        Effect for the target phase.
    """
    transitions = phase_def.transitions
    target = getattr(transitions, transition_key, None)

    if target is None:
        # No transition defined — fail
        return ExitFailureEffect(
            reason=f"No {transition_key} transition defined for phase '{state.phase}'"
        )

    if target == "failed":
        return ExitFailureEffect(reason=f"Phase '{state.phase}' failed")

    if target == "complete":
        return ExitSuccessEffect()

    # Return an effect that will advance to the target phase
    # The runner will set up the state for the new phase
    return _advance_to_phase(state, target)


def _advance_to_phase(state: PipelineState, target_phase: str) -> Effect:
    """Create an effect to advance to a new phase.

    Args:
        state: Current pipeline state.
        target_phase: Phase to advance to.

    Returns:
        Effect to prepare the target phase.
    """
    return PreparePromptEffect(phase=target_phase, iteration=state.iteration)


def _handle_unknown_phase(state: PipelineState) -> Effect:
    """Handle the case where the current phase is not in the policy.

    Args:
        state: Current pipeline state.

    Returns:
        ExitFailureEffect with an informative message.
    """
    msg = f"Unknown phase '{state.phase}' — no handler registered"
    logger.error(msg)
    return ExitFailureEffect(reason=msg)


def get_phase_drain(
    phase: PipelinePhase,
    pipeline_policy: PipelinePolicy,
) -> str | None:
    """Get the drain name for a given phase.

    Args:
        phase: Phase name.
        pipeline_policy: Loaded pipeline policy.

    Returns:
        Drain name or None if phase is not found.
    """
    phase_def = pipeline_policy.phases.get(phase)
    return phase_def.drain if phase_def else None


def resolve_next_phase(
    current_phase: PipelinePhase,
    signal: str,
    pipeline_policy: PipelinePolicy,
) -> PipelinePhase:
    """Resolve the next phase based on a signal and the pipeline policy.

    This is the core routing function used by the event handlers.
    Given a signal (success, failure, loopback), find the target phase
    from the current phase's transition table.

    Args:
        current_phase: Current phase name.
        signal: Signal type (success, failure, loopback).
        pipeline_policy: Loaded pipeline policy.

    Returns:
        Next phase name.

    Raises:
        ValueError: If the transition target is invalid.
    """
    phase_def = pipeline_policy.phases.get(current_phase)
    if phase_def is None:
        msg = f"Cannot resolve transition: phase '{current_phase}' not found"
        raise ValueError(msg)

    transitions = phase_def.transitions

    target: str | None
    if signal == "success":
        target = transitions.on_success
    elif signal == "failure":
        target = transitions.on_failure
    elif signal == "loopback":
        target = transitions.on_loopback
    else:
        msg = f"Unknown signal: {signal}"
        raise ValueError(msg)

    if target is None:
        msg = (
            f"No '{signal}' transition defined for phase '{current_phase}'. "
            f"Define on_{signal} in pipeline.toml or set the phase to terminal."
        )
        raise ValueError(msg)

    if target in ("failed", "complete"):
        # Terminal pseudo-phases are returned as-is; the runner handles them
        return target

    if target not in pipeline_policy.phases:
        msg = (
            f"Transition from '{current_phase}' on signal '{signal}' "
            f"references unknown phase '{target}'"
        )
        raise ValueError(msg)

    return target
