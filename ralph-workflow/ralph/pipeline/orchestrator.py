"""Pure orchestrator: determine next effect from state and policy.

This module implements the core routing logic that was previously hardcoded
in the reducer's match arms. Given the current PipelineState and the loaded
PipelinePolicy + AgentsPolicy, determine_next_effect() returns the next
Effect to execute.

No I/O, no side effects — fully deterministic and testable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ralph.config.enums import PipelinePhase
from ralph.pipeline import handoffs as phase_handoffs
from ralph.pipeline.effects import (
    Effect,
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


TransitionKey = Literal["on_success", "on_failure", "on_loopback"]


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
    - Budget counters (budget_caps / outer_progress, derived remaining)

    Args:
        state: Current pipeline state.
        pipeline_policy: Loaded pipeline policy (phase graph from pipeline.toml).
        agents_policy: Loaded agents policy (chains and drain bindings).

    Returns:
        The next Effect to execute.
    """
    terminal_phase = pipeline_policy.terminal_phase
    failed_route = pipeline_policy.recovery.failed_route

    # Terminal success state
    if state.phase == terminal_phase:
        return ExitSuccessEffect()

    # Terminal failure state — recover by routing to the pre-failure phase
    if state.phase == failed_route:
        target_phase = state.previous_phase or state.policy_entry_phase
        if target_phase == failed_route or target_phase is None:
            raise RuntimeError(
                f"Cannot determine recovery target for failed_route phase "
                f"'{failed_route}': both previous_phase and policy_entry_phase "
                f"are unset or recursion prevented routing. "
                f"Ensure policy_entry_phase is set in the initial state."
            )
        return PreparePromptEffect(
            phase=target_phase,
            drain=state.current_drain,
            previous_phase=state.previous_phase,
        )

    # Look up the current phase definition
    phase_def = pipeline_policy.phases.get(state.phase)
    if phase_def is None:
        return _handle_unknown_phase(state)

    drain = phase_def.drain
    chain_name = agents_policy.agent_drains[drain].chain
    chain = agents_policy.agent_chains[chain_name]

    # Routing based on phase type and state flags
    return _derive_effect_for_phase(state, phase_def, chain, chain_name, pipeline_policy)


def _derive_effect_for_phase(
    state: PipelineState,
    phase_def: PhaseDefinition,
    chain: AgentChainConfig,
    chain_name: str,
    pipeline_policy: PipelinePolicy,
) -> Effect:
    """Derive the next effect for a known phase.

    Args:
        state: Current pipeline state.
        phase_def: Phase definition from pipeline policy.
        chain: Agent chain config for the current drain.
        chain_name: Name of the agent chain.
        pipeline_policy: Loaded pipeline policy for terminal state resolution.

    Returns:
        Next Effect to execute.
    """
    phase = state.phase

    # skip_invocation phases route immediately to the next phase without invoking an agent
    if phase_def.skip_invocation:
        return _route_transition(state, phase_def, "on_success", pipeline_policy)

    # Check if we need to invoke the agent or prepare the prompt first
    if not _is_agent_invoked_for_phase(state, phase_def):
        # First time in this phase — prepare prompt then invoke agent
        return PreparePromptEffect(
            phase=phase,
            drain=phase_def.drain,
            previous_phase=state.previous_phase,
        )

    return InvokeAgentEffect(
        agent_name=_current_agent_name(state, chain),
        phase=phase,
        prompt_file="PROMPT.md",
        drain=phase_def.drain,
        chain_name=chain_name,
    )


def _is_agent_invoked_for_phase(state: PipelineState, phase_def: PhaseDefinition) -> bool:
    """Check whether the agent has been invoked for the given phase.

    This is a state-based check. In the current design, once an agent
    is invoked for a phase, a corresponding flag is set in the state.

    Policy-driven: commit-role phases are identified by their role attribute,
    not by hardcoded phase names.

    Args:
        state: Current pipeline state.
        phase_def: Phase definition from pipeline policy.

    Returns:
        True if the agent for this phase has been invoked.
    """
    # For commit-role phases, check the commit state via phase role, not phase name
    if phase_def.role == "commit":
        return state.commit.agent_invoked

    # For review phases, check review_issues_found (role-based check)
    if phase_def.role == "review":
        # Review "clean" means the phase ran but found no issues
        # The actual logic is in the event handler
        pass

    # Default: no invocation tracked, always invoke
    return False


def _current_agent_name(
    state: PipelineState,
    chain: AgentChainConfig,
) -> str:
    """Get the current agent name from the phase chain state or policy chain."""
    phase_chain = state.chain_for_phase(state.phase)
    if phase_chain is not None and phase_chain.agents:
        idx = phase_chain.current_index
        if idx < len(phase_chain.agents):
            return phase_chain.agents[idx]
    return chain.agents[0]


def _route_transition(
    state: PipelineState,
    phase_def: PhaseDefinition,
    transition_key: TransitionKey,
    pipeline_policy: PipelinePolicy,
) -> Effect:
    """Route to the phase specified by a transition.

    Args:
        state: Current pipeline state.
        phase_def: Current phase definition.
        transition_key: Which transition to follow (on_success, on_failure, on_loopback).
        pipeline_policy: Loaded pipeline policy for terminal state resolution.

    Returns:
        Effect for the target phase.
    """
    transitions = phase_def.transitions
    transition_targets: dict[str, str | None] = {
        "on_success": transitions.on_success,
        "on_failure": transitions.on_failure,
        "on_loopback": transitions.on_loopback,
    }
    target = transition_targets[transition_key]

    if target is None:
        # No transition defined — re-enter the same phase instead of exiting.
        return PreparePromptEffect(phase=state.phase, previous_phase=state.previous_phase)

    terminal_phase = pipeline_policy.terminal_phase
    failed_route = pipeline_policy.recovery.failed_route

    if target == failed_route:
        return PreparePromptEffect(phase=state.phase, previous_phase=state.previous_phase)

    if target == terminal_phase:
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
    return PreparePromptEffect(phase=target_phase, previous_phase=state.phase)


def _handle_unknown_phase(state: PipelineState) -> Effect:
    """Handle the case where the current phase is not in the policy.

    Args:
        state: Current pipeline state.

    Returns:
        ExitFailureEffect with an informative message.

    Raises:
        PhaseHandlerNotFoundError: Always, when the phase is not found.
    """
    raise PhaseHandlerNotFoundError(state.phase)


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
    return phase_handoffs.resolve_phase_drain(phase, pipeline_policy)


def resolve_next_phase(
    current_phase: PipelinePhase,
    signal: str,
    pipeline_policy: PipelinePolicy,
) -> PipelinePhase:
    """Backward-compatible wrapper for centralized handoff resolution."""
    return phase_handoffs.resolve_next_phase(current_phase, signal, pipeline_policy)


def resolve_post_commit_phase(
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
) -> PipelinePhase:
    """Backward-compatible wrapper for centralized post-commit routing."""
    return phase_handoffs.resolve_post_commit_phase(state, pipeline_policy)
