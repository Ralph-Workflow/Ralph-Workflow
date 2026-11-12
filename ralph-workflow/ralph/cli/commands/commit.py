from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.agents.registry import AgentRegistry
from ralph.cli.commands._commit_attempt_context import CommitAttemptContext
from ralph.cli.commands._commit_chain_config import CommitChainConfig
from ralph.cli.commands._commit_plumbing_options import CommitPlumbingOptions
from ralph.config.loader import load_config
from ralph.display.context import DisplayContext, make_display_context
from ralph.display.parallel_display import phase_style_for_phase, resolve_active_display
from ralph.display.status_bar import StatusBarModel
from ralph.git.commit_result import CommitCreationResult, CommitCreationStatus
from ralph.git.commit_review import stage_commit_changes_safely
from ralph.git.operations import (
    create_commit,
    find_repo_root,
    get_head_sha,
    has_staged_changes,
    list_changed_paths,
)
from ralph.mcp.artifacts.commit_message import (
    delete_commit_message_artifacts,
    read_commit_message_artifact,
)
from ralph.mcp.artifacts.completion_receipts import commit_receipt_matches_changed_files
from ralph.pipeline.factory import DefaultPipelineFactory
from ralph.pipeline.integration_resolution import inspect_integration_resolution
from ralph.pipeline.plumbing.commit_integration import (
    commit_integration_blocker,
    prepare_commit_integration,
)
from ralph.pipeline.plumbing.commit_plumbing import (
    CommitAgentResult,
    _generate_commit_message_with_agent,
    _render_commit_agent_activity_line,
    _start_commit_bridge,
    _write_commit_prompt_file,
    collect_commit_agent_output,
    invoke_commit_agent_attempt,
    run_commit_plumbing,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.policy.loader import load_agents_policy_for_workspace_scope
from ralph.prompts._commit_diff import commit_generation_diff
from ralph.prompts.master_prompt import materialize_master_prompt
from ralph.prompts.materialize import submit_artifact_tool_name_for_transport
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.agents.invoke import AgentInvocationError, invoke_agent
    from ralph.config.models import UnifiedConfig
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.policy.models import AgentsPolicy
    from ralph.pro_support.hooks import ProPipelineHooks

__all__ = [
    "AgentInvocationError",
    "CommitAgentResult",
    "CommitAttemptContext",
    "CommitPlumbingOptions",
    "_generate_commit_message_with_agent",
    "collect_commit_agent_output",
    "commit_plumbing",
    "invoke_agent",
    "invoke_commit_agent_attempt",
    "materialize_master_prompt",
    "submit_artifact_tool_name_for_transport",
]


def __getattr__(name: str) -> object:
    if name in ("AgentInvocationError", "invoke_agent"):
        from typing import cast

        from ralph.agents import invoke

        return cast("object", getattr(invoke, name))
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_MAX_DISPLAY_FILES = 5
_VERBOSE_THRESHOLD = 2


def commit_plumbing(
    *,
    options: CommitPlumbingOptions | None = None,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
) -> int:
    ctx = display_context if display_context is not None else make_display_context()
    display = resolve_active_display(None, ctx)
    opts = options or CommitPlumbingOptions()

    try:
        repo_root = find_repo_root()
    except Exception as e:
        display.emit_warning(f"Error: Not in a git repository: {e}")
        return 1

    try:
        workspace_scope = (
            None if opts.config_path is not None else resolve_workspace_scope(repo_root)
        )
        config = load_config(opts.config_path, opts.cli_overrides, workspace_scope=workspace_scope)
    except Exception as e:
        display.emit_warning(f"Error loading config: {e}")
        return 1

    if opts.show_commit_msg:
        _show_commit_message(repo_root, display_context=ctx)
        return 0

    if opts.generate_commit_msg or opts.generate_commit:
        display.update_status_bar(
            StatusBarModel(
                workspace_root=str(repo_root),
                phase_label="Commit Generation",
                phase_style=phase_style_for_phase("commit"),
            )
        )
        with display:
            return _handle_agent_commit_generation(
                repo_root=repo_root,
                config=config,
                options=opts,
                display_context=ctx,
                pro_hooks=pro_hooks,
                model_identity=model_identity,
            )
    if not has_staged_changes(repo_root):
        display.emit_warning("No staged changes to commit")
        return 1
    return 0


def _commit_dispatch_allowed(
    repo_root: Path,
    config: UnifiedConfig,
    *,
    display_context: DisplayContext,
    pro_hooks: ProPipelineHooks | None,
    model_identity: MultimodalModelIdentity | None,
) -> bool:
    if inspect_integration_resolution(repo_root, RebaseState()).dispatch_allowed:
        return True
    display = resolve_active_display(None, display_context)
    deps = DefaultPipelineFactory().build(
        config,
        display_context,
        pro_hooks=pro_hooks,
        model_identity=model_identity,
    )
    verdict = prepare_commit_integration(
        repo_root,
        config,
        deps,
        AgentRegistry.from_config(config),
        display_context,
    )
    if not verdict.dispatch_allowed:
        display.emit_warning(commit_integration_blocker(verdict))
        return False
    return True


def _persist_generated_commit_message(
    result: CommitAgentResult,
    repo_root: Path,
    *,
    display_context: DisplayContext,
) -> tuple[int, str | None]:
    display = resolve_active_display(None, display_context)
    if result.skipped:
        delete_commit_message_artifacts(repo_root)
        display.emit_warning("Skipping commit: agent requested skip")
        return 0, None
    if not result.message:
        display.emit_warning("Failed to generate commit message from commit drain agents")
        _print_commit_failure_details(result.failure_details, display_context=display_context)
        return 1, None
    persisted_message = read_commit_message_artifact(repo_root)
    if persisted_message is None:
        display.emit_warning("Failed to persist generated commit message")
        return 1, None
    display.emit_status("\nGenerated commit message:")
    display.emit_commit_message(repo_root)
    if result.failure_details:
        display.emit_warning("Recovered after retryable MCP/agent failures:")
        _print_commit_failure_details(result.failure_details, display_context=display_context)
    return 0, persisted_message


def _apply_generated_commit(
    repo_root: Path,
    persisted_message: str,
    *,
    git_user_name: str | None,
    git_user_email: str | None,
    display_context: DisplayContext,
) -> int:
    display = resolve_active_display(None, display_context)
    try:
        if (repo_root / ".git").exists() and not commit_receipt_matches_changed_files(
            repo_root,
            "commit-plumbing",
            "commit_message",
            tuple(list_changed_paths(repo_root)),
        ):
            display.emit_warning(
                "Commit failed: generated commit artifact is stale; regenerate before staging"
            )
            return 1
        head_before_stage = get_head_sha(repo_root) if (repo_root / ".git").exists() else ""
        stage_commit_changes_safely(repo_root)
        commit_result: CommitCreationResult = create_commit(
            repo_root,
            persisted_message,
            author_name=git_user_name,
            author_email=git_user_email,
            expected_head=head_before_stage,
        )
        if commit_result.status is CommitCreationStatus.ALREADY_ADVANCED:
            display.emit_warning("HEAD changed before commit; retaining commit artifact")
            return 1
        if commit_result.status is not CommitCreationStatus.CREATED or commit_result.sha is None:
            display.emit_warning("Commit failed: commit creation did not complete")
            return 1
        delete_commit_message_artifacts(repo_root)
        created_sha = commit_result.sha
        display.emit_status(f"Created commit: {created_sha[:8]}")
    except Exception as e:
        display.emit_warning(f"Commit failed: {e}")
        return 1
    return 0


def _handle_agent_commit_generation(
    *,
    repo_root: Path,
    config: UnifiedConfig,
    options: CommitPlumbingOptions,
    display_context: DisplayContext,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
) -> int:
    generate = options.generate_commit_msg or options.generate_commit
    apply = options.generate_commit
    git_user_name = config.general.git_user_name
    git_user_email = config.general.git_user_email

    if not generate:
        return 0

    inputs = _resolve_commit_generation_inputs(
        repo_root=repo_root,
        config=config,
        display_context=display_context,
        pro_hooks=pro_hooks,
        model_identity=model_identity,
    )
    if inputs is None:
        return 1
    diff, chain_config = inputs

    result = _generate_commit_message_with_chain(
        diff=diff,
        repo_root=repo_root,
        chain_config=chain_config,
        display_context=display_context,
        pro_hooks=pro_hooks,
        model_identity=model_identity,
    )

    status, persisted_message = _persist_generated_commit_message(
        result,
        repo_root,
        display_context=display_context,
    )
    if status != 0:
        return status
    if persisted_message is None or not apply:
        return 0
    return _apply_generated_commit(
        repo_root,
        persisted_message,
        git_user_name=git_user_name,
        git_user_email=git_user_email,
        display_context=display_context,
    )


def _resolve_commit_generation_inputs(
    *,
    repo_root: Path,
    config: UnifiedConfig,
    display_context: DisplayContext,
    pro_hooks: ProPipelineHooks | None,
    model_identity: MultimodalModelIdentity | None,
) -> tuple[str, CommitChainConfig] | None:
    display = resolve_active_display(None, display_context)
    if not _commit_dispatch_allowed(
        repo_root,
        config,
        display_context=display_context,
        pro_hooks=pro_hooks,
        model_identity=model_identity,
    ):
        return None
    delete_commit_message_artifacts(repo_root)
    diff = working_tree_diff(repo_root)
    if not diff.strip():
        display.emit_warning("No changes to commit")
        return None
    registry = AgentRegistry.from_config(config)
    workspace_scope = resolve_workspace_scope(repo_root)
    agents_policy = load_agents_policy_for_workspace_scope(workspace_scope, config=config)
    agents = _resolve_commit_message_agents(agents_policy)
    if not agents:
        display.emit_warning("No agents configured in the commit policy drain")
        return None
    chain_config = CommitChainConfig(
        registry=registry,
        agents=agents,
        verbose=config.general.verbosity >= _VERBOSE_THRESHOLD,
        agents_policy=agents_policy,
        general_config=config,
    )
    return diff, chain_config


def _show_commit_message(repo_root: Path, *, display_context: DisplayContext) -> None:
    display = resolve_active_display(None, display_context)
    commit_message = read_commit_message_artifact(repo_root)
    if commit_message is None:
        display.emit_warning("No commit message generated yet")
        return
    display.emit_commit_message(repo_root)


def _print_commit_failure_details(
    failure_details: list[str],
    *,
    display_context: DisplayContext | None = None,
) -> None:
    ctx = display_context if display_context is not None else make_display_context()
    display = resolve_active_display(None, ctx)
    for detail in failure_details:
        display.emit_warning(detail)


def _resolve_commit_message_agents(agents_policy: AgentsPolicy) -> list[str]:
    commit_binding = agents_policy.agent_drains.get("commit")
    if commit_binding is None:
        return []

    commit_chain = agents_policy.agent_chains[commit_binding.chain].agents
    ordered_candidates: list[str] = []
    for name in commit_chain:
        if name not in ordered_candidates:
            ordered_candidates.append(name)
    return ordered_candidates


def working_tree_diff(repo_root: Path) -> str:
    return commit_generation_diff(repo_root)


render_commit_agent_activity_line = _render_commit_agent_activity_line
write_commit_prompt_file = _write_commit_prompt_file
start_commit_bridge = _start_commit_bridge


def _generate_commit_message_with_chain(
    *,
    diff: str,
    repo_root: Path,
    chain_config: CommitChainConfig,
    display_context: DisplayContext,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
) -> CommitAgentResult:
    deps = DefaultPipelineFactory().build(
        cast("UnifiedConfig", chain_config.general_config),
        display_context,
        model_identity=model_identity,
        pro_hooks=pro_hooks,
    )
    return run_commit_plumbing(
        diff=diff,
        repo_root=repo_root,
        chain_config=chain_config,
        display_context=display_context,
        pipeline_deps=deps,
    )
