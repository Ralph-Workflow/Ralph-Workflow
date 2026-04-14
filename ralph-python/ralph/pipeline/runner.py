"""Main pipeline runner and effect handlers.

This module implements the event loop that drives the pipeline:
determine_effect(state) -> Effect -> Handler -> Event -> reduce(state, event) -> new_state

The runner coordinates between the orchestrator (pure effect determination),
the handlers (I/O execution), and the reducer (state transitions).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger
from rich.console import Console

from ralph.config.enums import (
    PHASE_COMPLETE,
    PHASE_FAILED,
    PHASE_PLANNING,
)
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline.effects import (
    CommitEffect,
    Effect,
    ExitFailureEffect,
    ExitSuccessEffect,
    InvokeAgentEffect,
    PreparePromptEffect,
    SaveCheckpointEffect,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, CommitState, PipelineState, RebaseState

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ralph.agents.registry import AgentRegistry
    from ralph.config.models import AgentConfig, UnifiedConfig

console = Console()


def run(config: UnifiedConfig, initial_state: PipelineState | None = None) -> int:
    """Execute the pipeline event loop.

    Args:
        config: Unified configuration for the pipeline.
        initial_state: Optional initial state (for resume from checkpoint).

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    state = initial_state or _create_initial_state(config)

    logger.info(
        "Starting pipeline: phase={}, iterations={}, reviews={}",
        state.phase,
        state.total_iterations,
        state.total_reviewer_passes,
    )

    try:
        while state.phase not in (PHASE_COMPLETE, PHASE_FAILED):
            # Determine next effect
            effect = _determine_effect(state, config)

            # Handle special effects that don't produce events
            if isinstance(effect, SaveCheckpointEffect):
                ckpt.save(state)
                new_state, _ = reducer_reduce(state, PipelineEvent.CHECKPOINT_SAVED)
                state = new_state
                continue

            if isinstance(effect, ExitSuccessEffect):
                console.print("[green]Pipeline completed successfully.[/green]")
                return 0

            if isinstance(effect, ExitFailureEffect):
                console.print(f"[red]Pipeline failed:[/red] {effect.reason}")
                return 1

            # Execute effect and get event
            event = _execute_effect(effect, config)

            # Reduce state
            state, _ = reducer_reduce(state, event)

            # Checkpoint after each cycle
            ckpt.save(state)

    except KeyboardInterrupt:
        logger.warning("Interrupted by user; saving checkpoint.")
        interrupted_state = state.copy_with(interrupted_by_user=True)
        ckpt.save(interrupted_state)
        return 130

    # Final state
    if state.phase == PHASE_COMPLETE:
        console.print("[green]Pipeline completed successfully.[/green]")
        return 0
    else:
        console.print(f"[red]Pipeline failed:[/red] {state.last_error or 'Unknown error'}")
        return 1


def _create_initial_state(config: UnifiedConfig) -> PipelineState:
    """Create initial pipeline state from configuration.

    Args:
        config: Unified configuration.

    Returns:
        Initial PipelineState.
    """
    # Set up agent chains from config
    dev_agents = config.agent_chains.get("development", [])
    rev_agents = config.agent_chains.get("review", [])

    return PipelineState(
        phase=PHASE_PLANNING,
        total_iterations=config.general.developer_iters,
        total_reviewer_passes=config.general.reviewer_reviews,
        dev_chain=AgentChainState(agents=dev_agents),
        rev_chain=AgentChainState(agents=rev_agents),
        rebase=RebaseState(),
        commit=CommitState(),
    )


def _determine_effect(state: PipelineState, config: UnifiedConfig) -> Effect:
    """Determine the next effect based on current state.

    Args:
        state: Current pipeline state.
        config: Unified configuration.

    Returns:
        Next Effect to execute.
    """
    del config
    phase_handlers: dict[str, Callable[[], Effect]] = {
        "planning": lambda: PreparePromptEffect(phase=state.phase, iteration=state.iteration),
        "development": lambda: _agent_or_advance(state, "review"),
        "review": lambda: _agent_or_next_phase(state, "development_commit"),
        "fix": lambda: _agent_or_next_phase(state, "review"),
        "development_commit": _commit_effect,
        "review_commit": _commit_effect,
        "complete": ExitSuccessEffect,
        "failed": lambda: ExitFailureEffect(reason=state.last_error or "Unknown failure"),
    }
    handler = phase_handlers.get(state.phase)
    if handler is None:
        return ExitFailureEffect(reason=f"Unknown phase: {state.phase}")
    return handler()


def _commit_effect() -> CommitEffect:
    return CommitEffect(message_file=".agent/tmp/commit_message.xml")


def _agent_or_advance(state: PipelineState, fallback_phase: str) -> Effect:
    """Get agent effect or advance to next phase/iteration.

    Args:
        state: Current pipeline state.
        fallback_phase: Phase to transition to if no agent or iteration exhausted.

    Returns:
        InvokeAgentEffect if agent available, otherwise PreparePromptEffect.
    """
    agent = state.current_agent()
    if agent:
        return InvokeAgentEffect(
            agent_name=agent,
            phase=state.phase,
            prompt_file="PROMPT.md",
        )
    if state.iteration + 1 < state.total_iterations:
        return PreparePromptEffect(
            phase=state.phase,
            iteration=state.iteration + 1,
        )
    return PreparePromptEffect(phase=fallback_phase, iteration=0)


def _agent_or_next_phase(state: PipelineState, fallback_phase: str) -> Effect:
    """Get agent effect or transition to fallback phase.

    Args:
        state: Current pipeline state.
        fallback_phase: Phase to transition to if no agent.

    Returns:
        InvokeAgentEffect if agent available, otherwise PreparePromptEffect.
    """
    agent = state.current_agent()
    if agent:
        return InvokeAgentEffect(
            agent_name=agent,
            phase=state.phase,
            prompt_file="PROMPT.md",
        )
    return PreparePromptEffect(phase=fallback_phase, iteration=0)


def _execute_effect(effect: Effect, config: UnifiedConfig) -> PipelineEvent:
    """Execute an effect and return the resulting event.

    Args:
        effect: Effect to execute.
        config: Unified configuration.

    Returns:
        Event resulting from effect execution.
    """
    from ralph.agents.invoke import AgentInvocationError, invoke_agent  # noqa: PLC0415
    from ralph.agents.registry import AgentRegistry  # noqa: PLC0415
    from ralph.git.operations import create_commit, stage_all  # noqa: PLC0415

    if isinstance(effect, InvokeAgentEffect):
        return _execute_agent_effect(
            effect, config, invoke_agent, AgentInvocationError, AgentRegistry
        )
    if isinstance(effect, CommitEffect):
        return _execute_commit_effect(create_commit, stage_all)
    if isinstance(effect, SaveCheckpointEffect):
        return PipelineEvent.CHECKPOINT_SAVED

    logger.warning("Unknown effect type: {}", type(effect))
    return PipelineEvent.AGENT_FAILURE


def _execute_agent_effect(
    effect: InvokeAgentEffect,
    config: UnifiedConfig,
    invoke_agent: Callable[[AgentConfig, str], Iterable[object]],
    agent_invocation_error: type[Exception],
    agent_registry: type[AgentRegistry],
) -> PipelineEvent:
    console.print(f"[cyan]Invoking agent:[/cyan] {effect.agent_name}")
    registry = agent_registry.from_config(config)
    agent_config = registry.get(effect.agent_name)
    if agent_config is None:
        logger.error("Agent not found: {}", effect.agent_name)
        return PipelineEvent.AGENT_FAILURE

    try:
        for _ in invoke_agent(agent_config, effect.prompt_file):
            pass
    except agent_invocation_error as exc:
        logger.error("Agent invocation failed: {}", exc)
        return PipelineEvent.AGENT_FAILURE
    except Exception:
        logger.exception("Unexpected error during agent invocation: {}")
        return PipelineEvent.AGENT_FAILURE
    return PipelineEvent.AGENT_SUCCESS


def _execute_commit_effect(
    create_commit: Callable[[str, str], str],
    stage_all: Callable[[str], None],
) -> PipelineEvent:
    try:
        stage_all(".")
        sha = create_commit(".", "Pipeline-generated commit")
        logger.info("Created commit: {}", sha[:8])
    except Exception as exc:
        logger.error("Commit failed: {}", exc)
        return PipelineEvent.COMMIT_FAILURE
    return PipelineEvent.COMMIT_SUCCESS
