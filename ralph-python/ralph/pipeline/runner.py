"""Main pipeline runner and effect handlers.

This module implements the event loop that drives the pipeline:
determine_effect(state) -> Effect -> Handler -> Event -> reduce(state, event) -> new_state

The runner coordinates between the orchestrator (pure effect determination),
the handlers (I/O execution), and the reducer (state transitions).
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from git import Repo
from loguru import logger
from rich.console import Console
from rich.text import Text

from ralph.agents.chain import ChainManager
from ralph.agents.parsers import AgentOutputLine, AgentParser, get_parser
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import (
    PHASE_COMPLETE,
    PHASE_FAILED,
    PHASE_PLANNING,
)
from ralph.display.phase_banner import show_phase_complete, show_phase_start, show_phase_transition
from ralph.mcp.capability_mapping import DrainClass, drain_class_for_session
from ralph.mcp.commit_message import (
    COMMIT_MESSAGE_ARTIFACT,
    delete_commit_message_artifacts,
    read_commit_message_from_path,
)
from ralph.mcp.server.lifecycle import shutdown_mcp_server, start_mcp_server
from ralph.mcp.session import MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV, AgentSession
from ralph.phases import PhaseContext, handle_phase
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
from ralph.pipeline.handoffs import resolve_phase_drain
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, CommitState, PipelineState, RebaseState
from ralph.policy.loader import load_policy_or_die
from ralph.prompts.materialize import (
    materialize_prompt_for_phase,
    prompt_file_for_phase,
    tool_name_prefix_for_transport,
)
from ralph.prompts.system_prompt import materialize_system_prompt
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace import FsWorkspace
from ralph.workspace.scope import WorkspaceScope, resolve_workspace_scope

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.policy.models import AgentsPolicy, PhaseDefinition, PipelinePolicy, PolicyBundle


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
_MAX_TEXT_LENGTH = 200
_MAX_TOOL_INPUT_LENGTH = 120
_MAX_TOOL_RESULT_LENGTH = 150
_MAX_TOOL_RESULT_BRIEF = 80
_TOOL_RESULT_BRIEF_THRESHOLD = 500
_MAX_METADATA_SUMMARY_LENGTH = 120


def _terminal_width() -> int:
    """Return the current terminal width with a safe fallback."""
    return shutil.get_terminal_size().columns or 80


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
    workspace_scope = resolve_workspace_scope()
    policy_bundle = load_policy_or_die(workspace_scope.root / ".agent")
    registry = AgentRegistry.from_config(config)
    state = initial_state or _create_initial_state(
        config,
        agents_policy=policy_bundle.agents,
        pipeline_policy=policy_bundle.pipeline,
    )

    logger.info(
        "Starting pipeline: phase={}, iterations={}, reviews={}",
        state.phase,
        state.total_iterations,
        state.total_reviewer_passes,
    )

    show_phase_start(state.phase, console=console)

    try:
        while state.phase not in (PHASE_COMPLETE, PHASE_FAILED):
            previous_phase = state.phase

            effect = _determine_effect_from_policy(state, policy_bundle, workspace_scope)
            inline_result = _handle_inline_effect(
                effect=effect,
                state=state,
                pipeline_policy=policy_bundle.pipeline,
                workspace_scope=workspace_scope,
            )
            if inline_result is not None:
                if isinstance(inline_result, int):
                    return inline_result
                state = inline_result
                if state.phase != previous_phase:
                    _show_phase_transition_with_context(previous_phase, state)
                continue

            workspace = FsWorkspace(
                workspace_scope.root,
                allowed_roots=workspace_scope.allowed_roots,
            )
            _materialize_agent_prompt_if_needed(
                effect,
                workspace,
                policy_bundle.pipeline,
                registry,
                workspace_scope,
            )

            event = _execute_effect(effect, config, workspace_scope)
            if isinstance(effect, InvokeAgentEffect) and event == PipelineEvent.AGENT_SUCCESS:
                event = _phase_event_after_agent_run(
                    effect=effect,
                    config=config,
                    policy_bundle=policy_bundle,
                    workspace=workspace,
                )

            state, _ = reducer_reduce(state, event, policy_bundle.pipeline)
            ckpt.save(state)

            if state.phase != previous_phase:
                _show_phase_transition_with_context(previous_phase, state)

    except KeyboardInterrupt:
        logger.warning("Interrupted by user; saving checkpoint.")
        interrupted_state = state.copy_with(interrupted_by_user=True)
        ckpt.save(interrupted_state)
        return 130
    # Final state
    if state.phase == PHASE_COMPLETE:
        show_phase_complete("complete", console=console)
        console.print("[green]Pipeline completed successfully.[/green]")
        return 0
    else:
        show_phase_complete("failed", console=console)
        console.print(_status_text("Pipeline failed", state.last_error or "Unknown error", "red"))
        return 1


def _show_phase_transition_with_context(previous_phase: str, state: PipelineState) -> None:
    """Display a phase transition banner with iteration/review context."""
    context: dict[str, object] = {}
    if state.phase in {"development", "fix"}:
        context["iteration"] = f"{state.iteration + 1}/{state.total_iterations}"
    if state.phase == "review":
        context["pass"] = f"{state.reviewer_pass + 1}/{state.total_reviewer_passes}"

    show_phase_transition(
        previous_phase,
        state.phase,
        context=context if context else None,
        console=console,
    )


def _handle_inline_effect(
    *,
    effect: Effect,
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
    workspace_scope: WorkspaceScope,
) -> PipelineState | int | None:
    if isinstance(effect, SaveCheckpointEffect):
        ckpt.save(state)
        new_state, _ = reducer_reduce(state, PipelineEvent.CHECKPOINT_SAVED, pipeline_policy)
        return new_state

    if isinstance(effect, PreparePromptEffect):
        _materialize_prepared_prompt(effect, pipeline_policy, workspace_scope)
        updated_state = state.copy_with(
            phase=effect.phase,
            iteration=effect.iteration,
            current_drain=effect.drain or resolve_phase_drain(effect.phase, pipeline_policy),
        )
        ckpt.save(updated_state)
        return updated_state

    if isinstance(effect, ExitSuccessEffect):
        show_phase_complete("complete", console=console)
        console.print("[green]Pipeline completed successfully.[/green]")
        return 0

    if isinstance(effect, ExitFailureEffect):
        show_phase_complete("failed", console=console)
        console.print(_status_text("Pipeline failed", effect.reason, "red"))
        return 1

    return None


def _materialize_prepared_prompt(
    effect: PreparePromptEffect,
    pipeline_policy: PipelinePolicy,
    workspace_scope: WorkspaceScope,
) -> None:
    workspace = FsWorkspace(
        workspace_scope.root,
        allowed_roots=workspace_scope.allowed_roots,
    )
    materialize_prompt_for_phase(
        phase=effect.phase,
        workspace=workspace,
        pipeline_policy=pipeline_policy,
        session_caps=SessionCapabilities.defaults_for_drain(
            _prompt_session_drain_for_phase(
                effect.drain or resolve_phase_drain(effect.phase, pipeline_policy) or effect.phase
            )
        ),
        workspace_root=workspace_scope.root,
    )


def _materialize_agent_prompt_if_needed(
    effect: Effect,
    workspace: FsWorkspace,
    pipeline_policy: PipelinePolicy,
    registry: _RegistryLike,
    workspace_scope: WorkspaceScope,
) -> None:
    if not isinstance(effect, InvokeAgentEffect):
        return

    agent = registry.get(effect.agent_name)
    tool_name_prefix = ""
    if agent is not None:
        tool_name_prefix = tool_name_prefix_for_transport(agent.transport)

    materialize_prompt_for_phase(
        phase=effect.phase,
        workspace=workspace,
        pipeline_policy=pipeline_policy,
        session_caps=SessionCapabilities.defaults_for_drain(
            _prompt_session_drain_for_phase(
                effect.drain or resolve_phase_drain(effect.phase, pipeline_policy) or effect.phase
            ),
            tool_name_prefix=tool_name_prefix,
        ),
        workspace_root=workspace_scope.root,
    )


def _create_initial_state(
    config: UnifiedConfig,
    *,
    agents_policy: AgentsPolicy | None = None,
    pipeline_policy: PipelinePolicy | None = None,
) -> PipelineState:
    """Create initial pipeline state from configuration.

    Args:
        config: Unified configuration.

    Returns:
        Initial PipelineState.
    """
    # Set up agent chains from config
    planning_agents = _agents_for_phase(
        config,
        "planning",
        agents_policy=agents_policy,
        pipeline_policy=pipeline_policy,
    )
    dev_agents = _agents_for_phase(
        config,
        "development",
        agents_policy=agents_policy,
        pipeline_policy=pipeline_policy,
    )
    dev_analysis_agents = _agents_for_phase(
        config,
        "development_analysis",
        agents_policy=agents_policy,
        pipeline_policy=pipeline_policy,
    )
    rev_agents = _agents_for_phase(
        config,
        "review",
        agents_policy=agents_policy,
        pipeline_policy=pipeline_policy,
    )
    review_analysis_agents = _agents_for_phase(
        config,
        "review_analysis",
        agents_policy=agents_policy,
        pipeline_policy=pipeline_policy,
    )
    fix_agents = _agents_for_phase(
        config,
        "fix",
        agents_policy=agents_policy,
        pipeline_policy=pipeline_policy,
    )
    entry_phase = pipeline_policy.entry_phase if pipeline_policy is not None else PHASE_PLANNING

    return PipelineState(
        phase=entry_phase,
        total_iterations=config.general.developer_iters,
        total_reviewer_passes=config.general.reviewer_reviews,
        development_budget_remaining=config.general.developer_iters,
        review_budget_remaining=config.general.reviewer_reviews,
        planning_chain=AgentChainState(agents=planning_agents),
        dev_chain=AgentChainState(agents=dev_agents),
        dev_analysis_chain=AgentChainState(agents=dev_analysis_agents),
        rev_chain=AgentChainState(agents=rev_agents),
        review_analysis_chain=AgentChainState(agents=review_analysis_agents),
        fix_chain=AgentChainState(agents=fix_agents),
        rebase=RebaseState(),
        commit=CommitState(),
        policy_entry_phase=entry_phase,
        current_drain=(
            resolve_phase_drain(entry_phase, pipeline_policy)
            if pipeline_policy is not None
            else None
        ),
    )


def _determine_effect_from_policy(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    workspace_scope: WorkspaceScope,
) -> Effect:
    if state.phase == PHASE_COMPLETE:
        return ExitSuccessEffect()

    if state.phase == PHASE_FAILED:
        return ExitFailureEffect(reason=state.last_error or "Unknown failure")

    phase_def = policy_bundle.pipeline.phases.get(state.phase)
    if phase_def is None:
        return ExitFailureEffect(reason=f"Unknown phase: {state.phase}")

    if phase_def.requires_commit:
        return _commit_phase_effect(state, policy_bundle, phase_def, workspace_scope)

    agent_name = _agent_name_for_phase_from_policy(state, policy_bundle)
    if agent_name is None:
        return ExitFailureEffect(reason=f"No agent configured for phase '{state.phase}'")

    return InvokeAgentEffect(
        agent_name=agent_name,
        phase=state.phase,
        prompt_file=prompt_file_for_phase(state.phase),
        drain=phase_def.drain,
    )


def _commit_phase_effect(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    phase_def: PhaseDefinition,
    workspace_scope: WorkspaceScope,
) -> Effect:
    if state.commit.agent_invoked:
        return _commit_effect(workspace_scope.root)
    agent_name = _agent_name_for_phase_from_policy(state, policy_bundle)
    if agent_name is None:
        return ExitFailureEffect(reason=f"No agent configured for commit phase '{state.phase}'")
    return InvokeAgentEffect(
        agent_name=agent_name,
        phase=state.phase,
        prompt_file=prompt_file_for_phase(state.phase),
        drain=phase_def.drain,
    )


def _agents_for_phase(
    config: UnifiedConfig,
    phase: str,
    *,
    agents_policy: AgentsPolicy | None = None,
    pipeline_policy: PipelinePolicy | None = None,
) -> list[str]:
    if agents_policy is not None and pipeline_policy is not None:
        phase_def = pipeline_policy.phases.get(phase)
        if phase_def is not None:
            binding = agents_policy.agent_drains.get(phase_def.drain)
            if binding is not None:
                chain = agents_policy.agent_chains.get(binding.chain)
                if chain is not None:
                    return list(chain.agents)

    drains = config.agent_drains if isinstance(config.agent_drains, dict) else {}
    chains = config.agent_chains if isinstance(config.agent_chains, dict) else {}
    chain_name = drains.get(phase) or phase
    return list(chains.get(chain_name, []))


def _agent_name_for_phase_from_policy(
    state: PipelineState,
    policy_bundle: PolicyBundle,
) -> str | None:
    current_agent = state.current_agent()
    if current_agent is not None:
        return current_agent

    phase_def = policy_bundle.pipeline.phases.get(state.phase)
    if phase_def is None:
        return None

    binding = policy_bundle.agents.agent_drains.get(phase_def.drain)
    if binding is None:
        return None

    chain = policy_bundle.agents.agent_chains.get(binding.chain)
    if chain is None or not chain.agents:
        return None
    return chain.agents[0]


def _phase_event_after_agent_run(
    *,
    effect: InvokeAgentEffect,
    config: UnifiedConfig,
    policy_bundle: PolicyBundle,
    workspace: FsWorkspace,
) -> PipelineEvent:
    ctx = PhaseContext.model_construct(
        workspace=workspace,
        registry=AgentRegistry.from_config(config),
        chain_manager=ChainManager(policy_bundle.agents),
        pipeline_policy=policy_bundle.pipeline,
        agents_policy=policy_bundle.agents,
        artifacts_policy=policy_bundle.artifacts,
        config=config,
    )
    events = handle_phase(effect, ctx)
    if not events:
        return PipelineEvent.AGENT_SUCCESS
    return events[0]


def _commit_effect(workspace_root: Path) -> CommitEffect:
    return CommitEffect(message_file=str(workspace_root / COMMIT_MESSAGE_ARTIFACT))


def _execute_effect(
    effect: Effect,
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
) -> PipelineEvent:
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
        return _execute_agent_effect(effect, config, deps, workspace_scope)
    if isinstance(effect, CommitEffect):
        return _execute_commit_effect(effect, create_commit, stage_all, workspace_scope.root)
    if isinstance(effect, SaveCheckpointEffect):
        return PipelineEvent.CHECKPOINT_SAVED

    logger.warning("Unknown effect type: {}", type(effect))
    return PipelineEvent.AGENT_FAILURE


def _execute_agent_effect(
    effect: InvokeAgentEffect,
    config: UnifiedConfig,
    deps: _AgentExecutionDeps,
    workspace_scope: WorkspaceScope,
) -> PipelineEvent:
    registry = deps.agent_registry.from_config(config)
    agent_config = registry.get(effect.agent_name)
    if agent_config is None:
        logger.error("Agent not found: {}", effect.agent_name)
        return PipelineEvent.AGENT_FAILURE

    show_phase_start(effect.phase, agent_name=effect.agent_name, console=console)

    bridge = None
    try:
        from ralph.agents.invoke import InvokeOptions  # noqa: PLC0415

        session = AgentSession(
            session_id=f"{effect.phase}-{uuid.uuid4().hex[:8]}",
            run_id=str(uuid.uuid4()),
            drain=effect.drain or effect.phase,
            capabilities=_default_mcp_capabilities_for_phase(effect.drain or effect.phase),
        )
        workspace = FsWorkspace(
            workspace_scope.root,
            allowed_roots=workspace_scope.allowed_roots,
        )
        bridge = start_mcp_server(session, workspace)

        options = InvokeOptions(
            verbose=config.general.verbosity >= _VERBOSE_LOG_LEVEL,
            workspace_path=workspace_scope.root,
            extra_env={
                MCP_ENDPOINT_ENV: bridge.agent_endpoint_uri(),
                MCP_RUN_ID_ENV: session.run_id,
            },
            system_prompt_file=materialize_system_prompt(
                workspace_root=workspace_scope.root,
                name=str(effect.phase),
            ),
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
        return PipelineEvent.AGENT_FAILURE
    except Exception:
        logger.exception("Unexpected error during agent invocation: {}")
        return PipelineEvent.AGENT_FAILURE
    finally:
        if bridge is not None:
            shutdown_mcp_server(bridge)
    return PipelineEvent.AGENT_SUCCESS


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
    repo_root: Path,
) -> PipelineEvent:
    try:
        message = _read_commit_effect_message(effect)
        if not message:
            logger.error("Commit message file is empty: {}", effect.message_file)
            return PipelineEvent.COMMIT_FAILURE
        if not _repo_has_commit_work(repo_root):
            logger.info("Skipping commit because the worktree is empty")
            _cleanup_commit_message_artifacts(repo_root)
            return PipelineEvent.COMMIT_SUCCESS
        stage_all(str(repo_root))
        sha = create_commit(str(repo_root), message)
        logger.info("Created commit: {}", sha[:8])
        _cleanup_commit_message_artifacts(repo_root)
    except Exception as exc:
        logger.error("Commit failed: {}", exc)
        return PipelineEvent.COMMIT_FAILURE
    return PipelineEvent.COMMIT_SUCCESS


def _read_commit_effect_message(effect: CommitEffect) -> str:
    return read_commit_message_from_path(Path(effect.message_file)) or ""


def _repo_has_commit_work(repo_root: Path) -> bool:
    return Repo(repo_root).is_dirty(untracked_files=True)


def _cleanup_commit_message_artifacts(repo_root: Path) -> None:
    delete_commit_message_artifacts(repo_root)


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


def _truncate(text: str, max_length: int) -> str:
    """Truncate text to max_length, appending ellipsis if truncated."""
    if len(text) <= max_length:
        return text
    return text[:max_length] + "…"


def _render_agent_activity_line(output: AgentOutputLine, agent_name: str) -> Text | None:
    rendered: Text | None = None

    if output.type == "text":
        content = output.content.strip()
        if content:
            rendered = _styled_prefix(agent_name, "white")
            rendered.append(_truncate(content, _MAX_TEXT_LENGTH))
    elif output.type == "tool_use":
        tool_name = output.content.strip() or "unknown-tool"
        rendered = _styled_prefix(f"{agent_name} tool", "magenta")
        rendered.append(tool_name, style="bold magenta")
        input_summary = _tool_input_summary(output.metadata)
        if input_summary:
            truncated = _truncate(input_summary, _MAX_TOOL_INPUT_LENGTH)
            rendered.append(f" ({truncated})", style="dim")
    elif output.type == "tool_result":
        result = output.content.strip()
        if result:
            rendered = _styled_prefix(f"{agent_name} result", "dim")
            if len(result) > _TOOL_RESULT_BRIEF_THRESHOLD:
                rendered.append(_truncate(result, _MAX_TOOL_RESULT_BRIEF), style="dim")
            else:
                rendered.append(_truncate(result, _MAX_TOOL_RESULT_LENGTH), style="dim")
    elif output.type == "error":
        error = output.content.strip() or "unknown error"
        rendered = _styled_prefix(f"{agent_name} ✗", "red")
        rendered.append(error, style="red")
    else:
        summary = _event_summary(output)
        rendered = _styled_prefix(f"{agent_name} {output.type}", "dim")
        rendered.append(summary)

    return rendered


def _styled_prefix(label: str, style: str) -> Text:
    text = Text()
    text.append(f"{label}:", style=style)
    text.append(" ")
    return text


def _status_text(label: str, detail: str, style: str) -> Text:
    text = Text()
    text.append(f"{label}:", style=style)
    text.append(" ")
    text.append(detail)
    return text


def _prompt_session_drain_for_phase(phase: str) -> SessionDrain:
    drain_map = {
        "planning": SessionDrain.PLANNING,
        "development": SessionDrain.DEVELOPMENT,
        "development_analysis": SessionDrain.DEVELOPMENT_ANALYSIS,
        "development_commit": SessionDrain.DEVELOPMENT_COMMIT,
        "review": SessionDrain.REVIEW,
        "review_analysis": SessionDrain.REVIEW_ANALYSIS,
        "review_commit": SessionDrain.REVIEW_COMMIT,
        "fix": SessionDrain.FIX,
    }
    return drain_map.get(phase, SessionDrain.COMMIT)


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
        result = "; ".join(parts)
        return _truncate(result, _MAX_METADATA_SUMMARY_LENGTH)

    for key, value_obj in metadata.items():
        value = _format_metadata_value(value_obj)
        if value:
            parts.append(f"{key}={value}")
        if len(parts) >= _MAX_METADATA_PARTS:
            break

    result = "; ".join(parts)
    return _truncate(result, _MAX_METADATA_SUMMARY_LENGTH)


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
