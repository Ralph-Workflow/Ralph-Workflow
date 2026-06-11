"""Commit plumbing commands for Ralph CLI.

This module is the thin CLI surface for ``commit`` and ``--generate-commit``.
All chain-iteration, retry-classification, and session-resume logic lives in
:mod:`ralph.pipeline.plumbing.commit_plumbing` and is invoked via
:func:`run_commit_plumbing`. The CLI surface only owns:

- option parsing (``CommitPlumbingOptions``),
- output formatting (Rich text rendering, exit codes),
- shell entry point (``commit_plumbing``).
"""

from __future__ import annotations

import typing
from typing import TYPE_CHECKING, cast

from rich.text import Text

from ralph.agents.invoke import AgentInvocationError, invoke_agent
from ralph.agents.registry import AgentRegistry
from ralph.api.opencode import validate_local_model_support
from ralph.cli.commands._commit_attempt_context import CommitAttemptContext
from ralph.cli.commands._commit_chain_config import CommitChainConfig
from ralph.cli.commands._commit_plumbing_options import CommitPlumbingOptions
from ralph.config.enums import AgentTransport
from ralph.config.loader import load_config
from ralph.display.context import DisplayContext, make_display_context
from ralph.display.parallel_display import resolve_active_display
from ralph.git.operations import (
    create_commit,
    find_repo_root,
    has_staged_changes,
    stage_all,
)
from ralph.mcp.artifacts.commit_message import (
    delete_commit_message_artifacts,
    read_commit_message_artifact,
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
from ralph.policy.loader import load_agents_policy_for_workspace_scope
from ralph.policy.models import AgentChainConfig, AgentDrainConfig
from ralph.prompts.materialize import submit_artifact_tool_name_for_transport
from ralph.prompts.system_prompt import materialize_system_prompt
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import AgentConfig, UnifiedConfig

# Re-exports for the test-patch surface.
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
    "materialize_system_prompt",
    "submit_artifact_tool_name_for_transport",
]


class _RepoHeadProtocol(typing.Protocol):
    def is_valid(self) -> bool: ...


class _RepoGitProtocol(typing.Protocol):
    def diff(self, *_args: object, **_kwargs: object) -> str: ...


class _RepoProtocol(typing.Protocol):
    head: _RepoHeadProtocol
    git: _RepoGitProtocol


class _RepoFactoryProtocol(typing.Protocol):
    def __call__(self, *_args: object, **_kwargs: object) -> _RepoProtocol: ...


# Maximum number of staged files to display in output
_MAX_DISPLAY_FILES = 5
_DEFAULT_COMMIT_AGENT = "claude"
_VERBOSE_THRESHOLD = 2
_MODELED_FLAG_PARTS = 2

Repo: _RepoFactoryProtocol | None = None


def commit_plumbing(
    *,
    options: CommitPlumbingOptions | None = None,
    display_context: DisplayContext | None = None,
) -> None:
    """Handle commit plumbing operations.

    Args:
        options: Commit plumbing options.
        display_context: Display context for consistent rendering.
            If None, a context is created using make_display_context().
    """
    ctx = display_context if display_context is not None else make_display_context()
    console = ctx.console
    opts = options or CommitPlumbingOptions()

    try:
        repo_root = find_repo_root()
    except Exception as e:
        console.print(
            _styled_commit_status("Error", f"Not in a git repository: {e}", "theme.status.error")
        )
        return

    # Load configuration
    try:
        workspace_scope = (
            None if opts.config_path is not None else resolve_workspace_scope(repo_root)
        )
        config = load_config(opts.config_path, opts.cli_overrides, workspace_scope=workspace_scope)
    except Exception as e:
        console.print(_styled_commit_status("Error loading config", str(e), "theme.status.error"))
        return

    if opts.show_commit_msg:
        _show_commit_message(repo_root, display_context=ctx)
        return

    if opts.generate_commit_msg or opts.generate_commit:
        _handle_agent_commit_generation(
            repo_root=repo_root,
            config=config,
            options=opts,
            display_context=ctx,
        )
        return

    if not has_staged_changes(repo_root):
        console.print(Text("No staged changes to commit", style="theme.status.warning"))
        return


def _handle_agent_commit_generation(
    *,
    repo_root: Path,
    config: UnifiedConfig,
    options: CommitPlumbingOptions,
    display_context: DisplayContext,
) -> None:
    ctx = display_context
    console = ctx.console
    generate = options.generate_commit_msg or options.generate_commit
    apply = options.generate_commit
    git_user_name = config.general.git_user_name
    git_user_email = config.general.git_user_email

    if not generate:
        return

    delete_commit_message_artifacts(repo_root)
    diff = working_tree_diff(repo_root)
    if not diff.strip():
        console.print(Text("No changes to commit", style="theme.status.warning"))
        return

    registry = AgentRegistry.from_config(config)
    agents = _resolve_commit_message_agents(config, registry)
    if not agents:
        console.print(
            Text(
                "No commit-capable agents available in commit/review drains",
                style="theme.status.error",
            )
        )
        return

    workspace_scope = resolve_workspace_scope(repo_root)
    result = _generate_commit_message_with_chain(
        diff=diff,
        repo_root=repo_root,
        chain_config=CommitChainConfig(
            registry=registry,
            agents=agents,
            verbose=config.general.verbosity >= _VERBOSE_THRESHOLD,
            agents_policy=load_agents_policy_for_workspace_scope(workspace_scope, config=config),
            general_config=config.general,
        ),
        display_context=ctx,
    )

    if result.skipped:
        delete_commit_message_artifacts(repo_root)
        console.print(Text("Skipping commit: agent requested skip", style="theme.status.warning"))
        return

    if not result.message:
        console.print(
            Text(
                "Failed to generate commit message from commit drain agents",
                style="theme.status.error",
            )
        )
        _print_commit_failure_details(result.failure_details, display_context=ctx)
        return

    persisted_message = read_commit_message_artifact(repo_root)
    if persisted_message is None:
        console.print(
            Text("Failed to persist generated commit message", style="theme.status.error")
        )
        return

    # Use the consolidated ParallelDisplay.emit_commit_message for consistent UI
    console.print(Text("\nGenerated commit message:", style="theme.status.success"))
    _display = resolve_active_display(None, ctx)
    _display.emit_commit_message(repo_root)
    if result.failure_details:
        console.print(
            Text(
                "Recovered after retryable MCP/agent failures:",
                style="theme.status.warning",
            )
        )
        _print_commit_failure_details(result.failure_details, display_context=ctx)

    if apply:
        try:
            stage_all(repo_root)
            sha = create_commit(
                repo_root,
                persisted_message,
                author_name=git_user_name,
                author_email=git_user_email,
            )
            delete_commit_message_artifacts(repo_root)
            console.print(
                _styled_commit_status(
                    "Created commit", sha[:8], "theme.status.success", leading_newline=True
                )
            )
        except Exception as e:
            console.print(
                _styled_commit_status(
                    "Commit failed", str(e), "theme.status.error", leading_newline=True
                )
            )


def _show_commit_message(repo_root: Path, *, display_context: DisplayContext) -> None:
    ctx = display_context
    console = ctx.console
    commit_message = read_commit_message_artifact(repo_root)
    if commit_message is None:
        console.print(Text("No commit message generated yet", style="theme.status.error"))
        return

    # Use the consolidated ParallelDisplay.emit_commit_message for consistent UI
    _display = resolve_active_display(None, display_context)
    _display.emit_commit_message(repo_root)


def _print_commit_failure_details(
    failure_details: list[str],
    *,
    display_context: DisplayContext,
) -> None:
    ctx = display_context
    console = ctx.console
    for detail in failure_details:
        console.print(Text(detail, style="theme.status.error"))


def _resolve_chain_agent_names(config: object, drain_name: str) -> list[str]:
    raw_agent_drains_obj: object = getattr(config, "agent_drains", {})
    raw_agent_drains = (
        cast("dict[str, object]", raw_agent_drains_obj)
        if isinstance(raw_agent_drains_obj, dict)
        else {}
    )
    raw_agent_chains_obj: object = getattr(config, "agent_chains", {})
    raw_agent_chains = (
        cast("dict[str, object]", raw_agent_chains_obj)
        if isinstance(raw_agent_chains_obj, dict)
        else {}
    )
    drain_binding = raw_agent_drains.get(drain_name)
    if isinstance(drain_binding, AgentDrainConfig):
        chain_name = drain_binding.chain
    elif isinstance(drain_binding, str):
        chain_name = drain_binding
    else:
        return []

    chain_value = raw_agent_chains.get(chain_name)
    if isinstance(chain_value, AgentChainConfig):
        return list(chain_value.agents)
    if isinstance(chain_value, list):
        return list(chain_value)
    return []


def _resolve_commit_message_agents(config: UnifiedConfig, registry: AgentRegistry) -> list[str]:
    commit_chain = _resolve_chain_agent_names(config, "commit")
    review_chain = _resolve_chain_agent_names(config, "review")

    commit_candidates = [
        name for name in commit_chain if _commit_drain_agent_supported(registry, name)
    ]
    review_candidates = [
        name for name in review_chain if _commit_drain_agent_supported(registry, name)
    ]
    default_candidates = [_DEFAULT_COMMIT_AGENT]
    default_supported = [
        name for name in default_candidates if _commit_drain_agent_supported(registry, name)
    ]

    ordered_candidates: list[str] = []
    for name in (*commit_candidates, *review_candidates, *default_supported):
        if name not in ordered_candidates:
            ordered_candidates.append(name)
    return ordered_candidates


def _commit_drain_agent_supported(registry: AgentRegistry, agent_name: str) -> bool:
    cfg = registry.get(agent_name)
    return cfg is not None and bool(cfg.can_commit) and _commit_agent_is_locally_supported(cfg)


def _commit_agent_is_locally_supported(agent: AgentConfig) -> bool:
    if agent.transport != AgentTransport.OPENCODE:
        return True
    model_id = _normalized_opencode_model_id(agent.model_flag)
    if model_id is None:
        return True
    command_name = agent.cmd.split()[0]
    return validate_local_model_support(model_id, command=command_name) is None


def _normalized_opencode_model_id(model_flag: str | None) -> str | None:
    if not model_flag:
        return None
    parts = model_flag.split()
    if len(parts) == _MODELED_FLAG_PARTS and parts[0] in {"-m", "--model"}:
        return parts[1].removeprefix("opencode/")
    if len(parts) == 1:
        return parts[0].removeprefix("opencode/")
    return None


def _styled_commit_status(
    label: str,
    detail: str,
    style: str,
    *,
    leading_newline: bool = False,
) -> Text:
    text = Text()
    if leading_newline:
        text.append("\n")
    text.append(f"{label}:", style=style)
    text.append(" ")
    text.append(detail)
    return text


def working_tree_diff(repo_root: Path) -> str:
    """Compute the working-tree diff used by the commit generator."""
    from ralph.executor.process import ProcessRunOptions, run_process
    from ralph.prompts.payload_refs import sanitize_surrogates

    if Repo is not None:
        repo = Repo(repo_root)
        try:
            if repo.head.is_valid():
                return sanitize_surrogates(repo.git.diff("HEAD"))
            return sanitize_surrogates(repo.git.diff("--cached"))
        finally:
            close = cast("typing.Callable[[], None] | None", getattr(repo, "close", None))
            if close is not None:
                close()

    head_check = run_process(
        "git",
        ["rev-parse", "--verify", "HEAD"],
        options=ProcessRunOptions(cwd=repo_root),
    )
    if head_check.returncode == 0:
        result = run_process("git", ["diff", "HEAD"], options=ProcessRunOptions(cwd=repo_root))
    else:
        result = run_process(
            "git",
            ["diff", "--cached"],
            options=ProcessRunOptions(cwd=repo_root),
        )
    if result.returncode != 0:
        return ""
    return sanitize_surrogates(result.stdout)


render_commit_agent_activity_line = _render_commit_agent_activity_line
write_commit_prompt_file = _write_commit_prompt_file
# ``start_commit_bridge`` is the legacy one-arg function; the plumbing
# module's ``_start_commit_bridge`` (which accepts ``agents_policy=``)
# is the one that ``run_commit_plumbing`` actually calls. Tests that
# need to stub it patch the module-level name on the plumbing package.
start_commit_bridge = _start_commit_bridge


def _generate_commit_message_with_chain(
    *,
    diff: str,
    repo_root: Path,
    chain_config: CommitChainConfig,
    display_context: DisplayContext,
) -> CommitAgentResult:
    """Thin legacy alias that delegates to :func:`run_commit_plumbing`.

    Preserved so the existing tests (which patch
    ``ralph.cli.commands.commit._generate_commit_message_with_chain``)
    continue to work. The actual chain-iteration ownership lives in
    :mod:`ralph.pipeline.plumbing.commit_plumbing`.
    """
    return run_commit_plumbing(
        diff=diff,
        repo_root=repo_root,
        chain_config=chain_config,
        display_context=display_context,
    )
