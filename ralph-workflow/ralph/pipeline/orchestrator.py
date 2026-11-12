from __future__ import annotations

import os
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
from ralph.pro_support.prompt import resolve_effective_prompt_path

if TYPE_CHECKING:
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import (
        AgentChainConfig,
        AgentsPolicy,
        PhaseDefinition,
        PipelinePolicy,
    )
    from ralph.recovery.controller import RecoveryController
    from ralph.workspace.scope import WorkspaceScope


class PhaseHandlerNotFoundError(Exception):
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
    workspace_scope: WorkspaceScope | None = None,
    *,
    recovery: RecoveryController | None = None,
) -> Effect:
    terminal_phase = pipeline_policy.terminal_phase
    failed_route = pipeline_policy.recovery.failed_route

    if state.phase in (terminal_phase,):
        return ExitSuccessEffect()

    if state.phase in (failed_route,):
        target_phase = state.previous_phase or state.policy_entry_phase
        if failed_route == target_phase or target_phase is None:
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

    phase_def = pipeline_policy.phases.get(state.phase)
    if phase_def is None:
        return _handle_unknown_phase(state)

    drain = phase_def.drain
    chain_name = agents_policy.agent_drains[drain].chain
    chain = agents_policy.agent_chains[chain_name]

    return _derive_effect_for_phase(
        state, phase_def, chain, chain_name, pipeline_policy, workspace_scope, recovery=recovery
    )


def _derive_effect_for_phase(
    state: PipelineState,
    phase_def: PhaseDefinition,
    chain: AgentChainConfig,
    chain_name: str,
    pipeline_policy: PipelinePolicy,
    workspace_scope: WorkspaceScope | None = None,
    *,
    recovery: RecoveryController | None = None,
) -> Effect:
    phase = state.phase

    if phase_def.skip_invocation:
        return _route_transition(state, phase_def, "on_success", pipeline_policy)

    if not _is_agent_invoked_for_phase(state, phase_def):
        return PreparePromptEffect(
            phase=phase,
            drain=phase_def.drain,
            previous_phase=state.previous_phase,
        )

    if workspace_scope is None:
        prompt_file = "PROMPT.md"
    else:
        prompt_file = str(resolve_effective_prompt_path(workspace_scope.root, os.environ))

    return InvokeAgentEffect(
        agent_name=_current_agent_name(state, chain, recovery=recovery),
        phase=phase,
        prompt_file=prompt_file,
        drain=phase_def.drain,
        chain_name=chain_name,
    )


def _is_agent_invoked_for_phase(state: PipelineState, phase_def: PhaseDefinition) -> bool:
    if phase_def.role == "commit":
        return state.commit.agent_invoked

    if phase_def.role == "review":
        pass

    return False


def _current_agent_name(
    state: PipelineState,
    chain: AgentChainConfig,
    *,
    recovery: RecoveryController | None = None,
) -> str:
    phase_chain = state.chain_for_phase(state.phase)
    agents = phase_chain.agents if phase_chain is not None and phase_chain.agents else chain.agents
    if recovery is not None and hasattr(recovery, "preferred_agent_index"):
        selection = recovery.preferred_agent_index(str(state.phase), agents)
        if selection.agent is not None:
            return selection.agent
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
    transitions = phase_def.transitions
    transition_targets: dict[str, str | None] = {
        "on_success": transitions.on_success,
        "on_failure": transitions.on_failure,
        "on_loopback": transitions.on_loopback,
    }
    target = transition_targets[transition_key]

    if target is None:
        return PreparePromptEffect(phase=state.phase, previous_phase=state.previous_phase)

    terminal_phase = pipeline_policy.terminal_phase
    failed_route = pipeline_policy.recovery.failed_route

    if target == failed_route:
        return PreparePromptEffect(phase=state.phase, previous_phase=state.previous_phase)

    if target == terminal_phase:
        return ExitSuccessEffect()

    return _advance_to_phase(state, target)


def _advance_to_phase(state: PipelineState, target_phase: str) -> Effect:
    return PreparePromptEffect(phase=target_phase, previous_phase=state.phase)


def _handle_unknown_phase(state: PipelineState) -> Effect:
    raise PhaseHandlerNotFoundError(state.phase)


def get_phase_drain(
    phase: PipelinePhase,
    pipeline_policy: PipelinePolicy,
) -> str | None:
    return phase_handoffs.resolve_phase_drain(phase, pipeline_policy)


def resolve_next_phase(
    current_phase: PipelinePhase,
    signal: str,
    pipeline_policy: PipelinePolicy,
) -> PipelinePhase:
    return phase_handoffs.resolve_next_phase(current_phase, signal, pipeline_policy)


def resolve_post_commit_phase(
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
) -> PipelinePhase:
    return phase_handoffs.resolve_post_commit_phase(state, pipeline_policy)
