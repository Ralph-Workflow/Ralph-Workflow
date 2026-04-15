"""Main pipeline runner and effect handlers.

This module implements the event loop that drives the pipeline:
determine_effect(state) -> Effect -> Handler -> Event -> reduce(state, event) -> new_state

The runner coordinates between the orchestrator (pure effect determination),
the handlers (I/O execution), and the reducer (state transitions).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger
from rich.console import Console

from ralph.agents.parsers import AgentOutputLine, AgentParser, get_parser
from ralph.config.enums import (
    PHASE_COMPLETE,
    PHASE_FAILED,
    PHASE_PLANNING,
)
from ralph.mcp.capability_mapping import DrainClass, drain_class_for_session
from ralph.mcp.commit_message import COMMIT_MESSAGE_ARTIFACT, read_commit_message_from_path
from ralph.mcp.server.lifecycle import (
    SessionBridgeLike,
    configure_mcp_server_session,
    shutdown_mcp_server,
    start_mcp_server,
)
from ralph.mcp.session_bridge import MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV, AgentSession
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
from ralph.workspace import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.models import AgentConfig, UnifiedConfig


class _InvokeAgentFn(Protocol):
    def __call__(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterable[object]: ...


class _RegistryLike(Protocol):
    def get(self, name: str) -> AgentConfig | None: ...


class _AgentRegistryFactory(Protocol):
    @classmethod
    def from_config(cls, config: UnifiedConfig) -> _RegistryLike: ...


console = Console()
_VERBOSE_LOG_LEVEL = 2
_AGENT_ACTIVITY_LOG_LEVEL = 1
_MAX_METADATA_PARTS = 3


@dataclass(frozen=True)
class _AgentExecutionDeps:
    invoke_agent: _InvokeAgentFn
    agent_invocation_error: type[Exception]
    agent_registry: _AgentRegistryFactory


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

    mcp_bridge: SessionBridgeLike | None = None
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

            if isinstance(effect, PreparePromptEffect):
                state = state.copy_with(
                    phase=effect.phase,
                    iteration=effect.iteration,
                )
                ckpt.save(state)
                continue

            if isinstance(effect, ExitSuccessEffect):
                console.print("[green]Pipeline completed successfully.[/green]")
                return 0

            if isinstance(effect, ExitFailureEffect):
                console.print(f"[red]Pipeline failed:[/red] {effect.reason}")
                return 1

            # Execute effect and get event
            event, mcp_bridge = _execute_effect(effect, config, mcp_bridge)

            # Reduce state
            state, _ = reducer_reduce(state, event)

            # Checkpoint after each cycle
            ckpt.save(state)

    except KeyboardInterrupt:
        logger.warning("Interrupted by user; saving checkpoint.")
        interrupted_state = state.copy_with(interrupted_by_user=True)
        ckpt.save(interrupted_state)
        return 130
    finally:
        if mcp_bridge is not None:
            try:
                shutdown_mcp_server(mcp_bridge)
            except Exception:
                logger.exception("Failed to shut down run-scoped MCP server")

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
    planner_agent = _planning_agent(config)
    phase_handlers: dict[str, Callable[[], Effect]] = {
        "planning": lambda: _planning_effect(state, planner_agent),
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


def _planning_effect(state: PipelineState, planner_agent: str | None) -> Effect:
    if planner_agent:
        return InvokeAgentEffect(
            agent_name=planner_agent,
            phase=state.phase,
            prompt_file="PROMPT.md",
        )
    return PreparePromptEffect(phase="development", iteration=0)


def _planning_agent(config: UnifiedConfig) -> str | None:
    planning_chain = config.agent_drains.get("planning")
    if planning_chain is None:
        return None

    planning_agents = config.agent_chains.get(planning_chain, [])
    if not planning_agents:
        return None

    return planning_agents[0]


def _commit_effect() -> CommitEffect:
    return CommitEffect(message_file=COMMIT_MESSAGE_ARTIFACT)


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


def _execute_effect(
    effect: Effect, config: UnifiedConfig, run_bridge: SessionBridgeLike | None
) -> tuple[PipelineEvent, SessionBridgeLike | None]:
    """Execute an effect and return the resulting event.

    Args:
        effect: Effect to execute.
        config: Unified configuration.

    Returns:
        Event resulting from effect execution.
    """
    from ralph.agents.invoke import (  # noqa: PLC0415
        AgentInvocationError,
        invoke_agent,
    )
    from ralph.agents.registry import AgentRegistry  # noqa: PLC0415
    from ralph.git.operations import create_commit, stage_all  # noqa: PLC0415

    deps = _AgentExecutionDeps(
        invoke_agent=invoke_agent,
        agent_invocation_error=AgentInvocationError,
        agent_registry=AgentRegistry,
    )

    if isinstance(effect, InvokeAgentEffect):
        return _execute_agent_effect(
            effect,
            config,
            deps,
            run_bridge,
        )
    if isinstance(effect, CommitEffect):
        return _execute_commit_effect(effect, create_commit, stage_all), run_bridge
    if isinstance(effect, SaveCheckpointEffect):
        return PipelineEvent.CHECKPOINT_SAVED, run_bridge

    logger.warning("Unknown effect type: {}", type(effect))
    return PipelineEvent.AGENT_FAILURE, run_bridge


def _execute_agent_effect(
    effect: InvokeAgentEffect,
    config: UnifiedConfig,
    deps: _AgentExecutionDeps,
    run_bridge: SessionBridgeLike | None = None,
) -> tuple[PipelineEvent, SessionBridgeLike | None]:
    console.print(f"[cyan]Invoking agent:[/cyan] {effect.agent_name}")
    registry = deps.agent_registry.from_config(config)
    agent_config = registry.get(effect.agent_name)
    if agent_config is None:
        logger.error("Agent not found: {}", effect.agent_name)
        return PipelineEvent.AGENT_FAILURE, run_bridge

    bridge = run_bridge
    owns_bridge = False
    try:
        from ralph.agents.invoke import InvokeOptions  # noqa: PLC0415

        session = AgentSession(
            session_id=f"{effect.phase}-{uuid.uuid4().hex[:8]}",
            run_id=str(uuid.uuid4()),
            drain=effect.phase,
            capabilities=_default_mcp_capabilities_for_phase(effect.phase),
        )
        workspace = FsWorkspace(Path())

        if bridge is None:
            bridge = start_mcp_server(session, workspace)
            owns_bridge = True
        else:
            configure_mcp_server_session(bridge, session)
        assert bridge is not None

        options = InvokeOptions(
            verbose=config.general.verbosity >= _VERBOSE_LOG_LEVEL,
            extra_env={
                MCP_ENDPOINT_ENV: bridge.agent_endpoint_uri(),
                MCP_RUN_ID_ENV: session.run_id,
            },
        )
        output_lines = deps.invoke_agent(agent_config, effect.prompt_file, options=options)
        if config.general.verbosity >= _AGENT_ACTIVITY_LOG_LEVEL:
            _stream_parsed_agent_activity(
                output_lines, str(agent_config.json_parser), effect.agent_name
            )
        else:
            for _ in output_lines:
                pass
    except deps.agent_invocation_error as exc:
        logger.error("Agent invocation failed: {}", exc)
        if owns_bridge and bridge is not None:
            shutdown_mcp_server(bridge)
        return PipelineEvent.AGENT_FAILURE, bridge
    except Exception:
        logger.exception("Unexpected error during agent invocation: {}")
        if owns_bridge and bridge is not None:
            shutdown_mcp_server(bridge)
        return PipelineEvent.AGENT_FAILURE, bridge
    return PipelineEvent.AGENT_SUCCESS, bridge


def _default_mcp_capabilities_for_phase(phase: str) -> set[str]:
    drain_class = drain_class_for_session(phase)
    base = {
        "workspace.read",
        "git.status_read",
        "git.diff_read",
        "artifact.submit",
    }

    if drain_class in {DrainClass.PLANNING, DrainClass.ANALYSIS, DrainClass.REVIEW}:
        return base
    if drain_class is DrainClass.COMMIT:
        return base | {"workspace.write_ephemeral", "git.write", "run.report_progress"}
    if drain_class in {DrainClass.DEVELOPMENT, DrainClass.FIX}:
        return base | {
            "workspace.write_ephemeral",
            "workspace.write_tracked",
            "process.exec_bounded",
            "run.report_progress",
            "env.read",
        }
    return base


def _execute_commit_effect(
    effect: CommitEffect,
    create_commit: Callable[[str, str], str],
    stage_all: Callable[[str], None],
) -> PipelineEvent:
    try:
        message = _read_commit_effect_message(effect)
        if not message:
            logger.error("Commit message file is empty: {}", effect.message_file)
            return PipelineEvent.COMMIT_FAILURE
        stage_all(".")
        sha = create_commit(".", message)
        logger.info("Created commit: {}", sha[:8])
    except Exception as exc:
        logger.error("Commit failed: {}", exc)
        return PipelineEvent.COMMIT_FAILURE
    return PipelineEvent.COMMIT_SUCCESS


def _read_commit_effect_message(effect: CommitEffect) -> str:
    return read_commit_message_from_path(Path(effect.message_file)) or ""


def _stream_parsed_agent_activity(
    lines: Iterable[object],
    parser_type: str,
    agent_name: str,
) -> None:
    parser = _resolve_parser(parser_type)
    str_lines = (str(line) for line in lines)
    for parsed_line in parser.parse(str_lines):
        rendered = _render_agent_activity_line(parsed_line, agent_name)
        if rendered is not None:
            console.print(rendered)


def _resolve_parser(parser_type: str) -> AgentParser:
    try:
        return get_parser(parser_type)
    except ValueError:
        logger.warning("Unknown parser '{}'; falling back to generic", parser_type)
        return get_parser("generic")


def _render_agent_activity_line(output: AgentOutputLine, agent_name: str) -> str | None:
    rendered: str | None = None

    if output.type == "text":
        content = output.content.strip()
        if content:
            rendered = f"[white]{agent_name}:[/white] {content}"
    elif output.type == "tool_use":
        tool_name = output.content.strip() or "unknown-tool"
        rendered = f"[magenta]{agent_name} tool:[/magenta] {tool_name}"
        input_summary = _tool_input_summary(output.metadata)
        if input_summary:
            rendered = f"{rendered} ({input_summary})"
    elif output.type == "tool_result":
        result = output.content.strip()
        if result:
            rendered = f"[dim]{agent_name} tool result:[/dim] {result}"
    elif output.type == "error":
        error = output.content.strip() or "unknown error"
        rendered = f"[red]{agent_name} error:[/red] {error}"
    else:
        summary = _event_summary(output)
        rendered = f"[dim]{agent_name} {output.type}:[/dim] {summary}"

    return rendered


def _event_summary(output: AgentOutputLine) -> str:
    content = output.content.strip()
    if content:
        return content

    if output.metadata:
        summary = _metadata_summary(output.metadata)
        if summary:
            return summary

    return "(no details)"


def _tool_input_summary(metadata: dict[str, object]) -> str:
    input_obj = metadata.get("input")
    if isinstance(input_obj, dict):
        return _metadata_summary(cast("dict[str, object]", input_obj))
    return ""


def _metadata_summary(metadata: dict[str, object]) -> str:
    preferred_keys = (
        "status",
        "summary",
        "phase",
        "tool",
        "name",
        "command",
        "workdir",
        "path",
        "result",
        "output",
        "error",
        "message",
    )

    parts: list[str] = []
    for key in preferred_keys:
        if key not in metadata:
            continue
        value = _format_metadata_value(metadata[key])
        if value:
            parts.append(f"{key}={value}")

    if parts:
        return "; ".join(parts)

    for key, value_obj in metadata.items():
        value = _format_metadata_value(value_obj)
        if value:
            parts.append(f"{key}={value}")
        if len(parts) >= _MAX_METADATA_PARTS:
            break

    return "; ".join(parts)


def _format_metadata_value(value: object) -> str:
    formatted = ""
    if isinstance(value, str):
        formatted = value.strip()
    elif isinstance(value, (bool, int, float)):
        formatted = str(value)
    elif isinstance(value, dict):
        dict_value = cast("dict[str, object]", value)
        nested = _metadata_summary(dict_value)
        formatted = nested or f"{len(dict_value)} field(s)"
    elif isinstance(value, list):
        if not value:
            return formatted
        formatted = _format_list_metadata_value(value)
    return formatted


def _format_list_metadata_value(value: list[object]) -> str:
    scalar_items: list[str] = []
    for item in value:
        if isinstance(item, (str, int, float, bool)):
            item_str = str(item).strip()
            if item_str:
                scalar_items.append(item_str)
        else:
            return f"{len(value)} item(s)"
    return ", ".join(scalar_items)
