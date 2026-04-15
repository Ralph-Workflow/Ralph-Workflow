"""Commit plumbing commands for Ralph CLI.

This module implements commit-related commands for generating
and applying commit messages.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from git import Repo
from rich.console import Console
from rich.panel import Panel

from ralph.agents.invoke import AgentInvocationError, InvokeOptions, invoke_agent
from ralph.agents.parsers import AgentOutputLine, AgentParser, get_parser
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport
from ralph.config.loader import load_config
from ralph.git.operations import (
    create_commit,
    find_repo_root,
    get_staged_files,
    has_staged_changes,
    stage_all,
)
from ralph.mcp.commit_message import (
    delete_commit_message_artifacts,
    read_commit_message_artifact,
)
from ralph.mcp.server.lifecycle import SessionBridgeLike, start_mcp_server
from ralph.mcp.session import MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV, AgentSession
from ralph.prompts.commit import prompt_commit_message, prompt_commit_message_for_opencode
from ralph.prompts.template_registry import TemplateRegistry, default_template_dirs
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from ralph.config.models import AgentConfig, UnifiedConfig

console = Console()

# Maximum number of staged files to display in output
_MAX_DISPLAY_FILES = 5
_DEFAULT_COMMIT_AGENT = "claude"
_VERBOSE_THRESHOLD = 2
_SKIP_PREFIX = "skip:"
_MAX_METADATA_PARTS = 5
_OPENCODE_SUBMIT_ARTIFACT_TOOL_NAME = "ralph_ralph_submit_artifact"


@dataclass(frozen=True)
class CommitPlumbingOptions:
    """Options for commit plumbing operations.

    Attributes:
        generate_commit_msg: Generate commit message without applying.
        apply_commit: Apply commit message without generating.
        generate_commit: Generate and apply commit.
        show_commit_msg: Show current commit message.
        config_path: Path to configuration file.
        cli_overrides: CLI flag overrides.
    """

    generate_commit_msg: bool = False
    apply_commit: bool = False
    generate_commit: bool = False
    show_commit_msg: bool = False
    config_path: Path | None = None
    cli_overrides: dict[str, object] | None = None


@dataclass(frozen=True)
class CommitAgentResult:
    message: str = ""
    skipped: bool = False
    failure_details: list[str] = field(default_factory=list)


def commit_plumbing(
    *,
    options: CommitPlumbingOptions | None = None,
) -> None:
    """Handle commit plumbing operations.

    Args:
        options: Commit plumbing options.
    """
    opts = options or CommitPlumbingOptions()

    try:
        repo_root = find_repo_root()
    except Exception as e:
        console.print(f"[red]Error:[/red] Not in a git repository: {e}")
        return

    # Load configuration
    try:
        config = load_config(opts.config_path, opts.cli_overrides)
    except Exception as e:
        console.print(f"[red]Error loading config:[/red] {e}")
        return

    if opts.show_commit_msg:
        _show_commit_message(repo_root)
        return

    if opts.generate_commit_msg or opts.generate_commit:
        _handle_agent_commit_generation(
            repo_root=repo_root,
            config=config,
            options=opts,
        )
        return

    if not has_staged_changes(repo_root):
        console.print("[yellow]No staged changes to commit[/yellow]")
        return


def _handle_agent_commit_generation(
    *,
    repo_root: Path,
    config: UnifiedConfig,
    options: CommitPlumbingOptions,
) -> None:
    generate = options.generate_commit_msg or options.generate_commit
    apply = options.generate_commit
    git_user_name = config.general.git_user_name
    git_user_email = config.general.git_user_email

    if not generate:
        return

    diff = _working_tree_diff(repo_root)
    if not diff.strip():
        console.print("[yellow]No changes to commit[/yellow]")
        return

    registry = AgentRegistry.from_config(config)
    agents = _resolve_commit_message_agents(config, registry)
    if not agents:
        console.print("[red]No commit-capable agents available in commit/review drains[/red]")
        return

    result = _generate_commit_message_with_chain(
        diff=diff,
        repo_root=repo_root,
        registry=registry,
        agents=agents,
        verbose=config.general.verbosity >= _VERBOSE_THRESHOLD,
    )

    if result.skipped:
        delete_commit_message_artifacts(repo_root)
        console.print("[yellow]Skipping commit: agent requested skip[/yellow]")
        return

    if not result.message:
        console.print("[red]Failed to generate commit message from commit drain agents[/red]")
        _print_commit_failure_details(result.failure_details)
        return

    persisted_message = read_commit_message_artifact(repo_root)
    if persisted_message is None:
        console.print("[red]Failed to persist generated commit message[/red]")
        return

    console.print("\n[green]Generated commit message:[/green]")
    console.print(Panel(persisted_message, border_style="green"))

    if apply:
        stage_all(repo_root)
        try:
            sha = create_commit(
                repo_root,
                persisted_message,
                author_name=git_user_name,
                author_email=git_user_email,
            )
            delete_commit_message_artifacts(repo_root)
            console.print(f"\n[green]Created commit:[/green] {sha[:8]}")
        except Exception as e:
            console.print(f"\n[red]Commit failed:[/red] {e}")


def _resolve_commit_message_agents(config: UnifiedConfig, registry: AgentRegistry) -> list[str]:
    commit_chain_name = config.agent_drains.get("commit")
    commit_chain = config.agent_chains.get(commit_chain_name, []) if commit_chain_name else []

    review_chain_name = config.agent_drains.get("review")
    review_chain = config.agent_chains.get(review_chain_name, []) if review_chain_name else []

    commit_candidates = [
        name for name in commit_chain if _commit_drain_agent_supported(registry, name)
    ]
    if commit_candidates:
        return commit_candidates

    review_candidates = [
        name for name in review_chain if _commit_drain_agent_supported(registry, name)
    ]
    if review_candidates:
        return review_candidates

    default_candidates = [_DEFAULT_COMMIT_AGENT]
    return [name for name in default_candidates if _commit_drain_agent_supported(registry, name)]


def _commit_drain_agent_supported(registry: AgentRegistry, agent_name: str) -> bool:
    cfg = registry.get(agent_name)
    return cfg is not None and bool(cfg.can_commit)


def _working_tree_diff(repo_root: Path) -> str:
    repo = Repo(repo_root)
    diff = cast("str", repo.git.diff("HEAD"))
    if not repo.untracked_files:
        return diff

    untracked_block = "\n".join(repo.untracked_files)
    if not untracked_block:
        return diff

    prefix = "\n\n" if diff.strip() else ""
    return f"{diff}{prefix}# Untracked files\n{untracked_block}\n"


def _commit_submit_artifact_tool_names(
    registry: AgentRegistry,
    agents: list[str],
) -> tuple[str, ...]:
    if any(_is_opencode_agent(registry.get(agent_name)) for agent_name in agents):
        return (_OPENCODE_SUBMIT_ARTIFACT_TOOL_NAME,)
    return ("ralph_submit_artifact",)


def _is_opencode_agent(agent: AgentConfig | None) -> bool:
    return agent is not None and agent.transport == AgentTransport.OPENCODE


def _commit_prompt_for_agent(
    agent: AgentConfig,
    diff: str,
    *,
    template_registry: TemplateRegistry,
) -> str:
    if _is_opencode_agent(agent):
        return prompt_commit_message_for_opencode(
            diff,
            submit_artifact_tool_name=_OPENCODE_SUBMIT_ARTIFACT_TOOL_NAME,
        )
    return prompt_commit_message(diff, template_registry=template_registry)


def _generate_commit_message_with_chain(
    *,
    diff: str,
    repo_root: Path,
    registry: AgentRegistry,
    agents: list[str],
    verbose: bool,
) -> CommitAgentResult:
    template_dirs = (repo_root / ".agent" / "prompts" / "commit", *default_template_dirs(repo_root))
    template_registry = TemplateRegistry(template_dirs=template_dirs)
    bridge = _start_commit_bridge(repo_root)
    extra_env = _commit_bridge_env(bridge)
    failure_details: list[str] = []

    try:
        for agent_name in agents:
            cfg = registry.get(agent_name)
            if cfg is None:
                continue
            prompt = _commit_prompt_for_agent(cfg, diff, template_registry=template_registry)
            prompt_file = _write_commit_prompt_file(repo_root, prompt)
            result = _generate_commit_message_with_agent(
                cfg,
                repo_root=repo_root,
                prompt_file=prompt_file,
                verbose=verbose,
                extra_env=extra_env,
            )
            failure_details.extend(result.failure_details)

            if result.skipped:
                return CommitAgentResult(skipped=True, failure_details=failure_details)
            if result.message:
                return CommitAgentResult(message=result.message)
    finally:
        bridge.shutdown()

    return CommitAgentResult(failure_details=failure_details)


def _generate_commit_message_with_agent(
    agent: AgentConfig,
    *,
    repo_root: Path,
    prompt_file: str,
    verbose: bool,
    extra_env: dict[str, str],
) -> CommitAgentResult:
    delete_commit_message_artifacts(repo_root)
    try:
        lines = invoke_agent(
            agent,
            prompt_file,
            options=InvokeOptions(
                verbose=verbose,
                workspace_path=repo_root,
                extra_env=extra_env,
                pure=_is_opencode_agent(agent),
            ),
        )
    except AgentInvocationError as exc:
        return CommitAgentResult(
            failure_details=[
                _format_agent_invocation_failure(agent.cmd, prompt_file, exc, parsed_output=[])
            ]
        )

    try:
        parsed_output = _collect_commit_agent_output(
            lines,
            parser_type=str(agent.json_parser),
            agent_name=agent.cmd.split()[0],
            verbose=verbose,
        )
    except AgentInvocationError as exc:
        return CommitAgentResult(
            failure_details=[
                _format_agent_invocation_failure(
                    agent.cmd,
                    prompt_file,
                    exc,
                    parsed_output=_parsed_output_from_invocation_error(exc),
                )
            ]
        )

    try:
        artifact_message = read_commit_message_artifact(repo_root)
    except Exception as exc:
        return CommitAgentResult(
            failure_details=[
                _format_commit_agent_failure(agent.cmd, prompt_file, parsed_output, str(exc))
            ]
        )

    if not artifact_message:
        return CommitAgentResult(
            failure_details=[
                _format_commit_agent_failure(
                    agent.cmd,
                    prompt_file,
                    parsed_output,
                    "agent completed without writing a commit_message artifact",
                )
            ]
        )

    if _is_skip_response(artifact_message):
        return CommitAgentResult(skipped=True)

    return CommitAgentResult(message=artifact_message)


def _is_skip_response(text: str) -> bool:
    return text.strip().lower().startswith(_SKIP_PREFIX)


def _write_commit_prompt_file(repo_root: Path, prompt: str) -> str:
    prompt_path = repo_root / ".agent" / "tmp" / "commit_prompt.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt, encoding="utf-8")
    return str(prompt_path)


def _show_commit_message(repo_root: Path) -> None:
    commit_message = read_commit_message_artifact(repo_root)
    if commit_message is None:
        console.print("[red]No commit message generated yet[/red]")
        return

    console.print("\n[green]Commit message:[/green]")
    console.print(Panel(commit_message, border_style="green"))


def _print_commit_failure_details(failure_details: list[str]) -> None:
    for detail in failure_details:
        console.print(Panel(detail, border_style="red", title="Commit drain failure"))


def _format_agent_invocation_failure(
    agent_name: str,
    prompt_file: str,
    exc: AgentInvocationError,
    *,
    parsed_output: list[str] | None = None,
) -> str:
    stderr = exc.stderr.strip() or "(no stderr)"
    lines = [
        f"Agent: {agent_name}",
        f"Prompt file: {prompt_file}",
        f"Exit code: {exc.returncode}",
    ]
    if parsed_output:
        lines.extend(["Agent output:", *parsed_output])
    lines.extend(["Stderr:", stderr])
    return "\n".join(lines)


def _format_commit_agent_failure(
    agent_name: str,
    prompt_file: str,
    parsed_output: list[str],
    reason: str,
) -> str:
    lines = [
        f"Agent: {agent_name}",
        f"Prompt file: {prompt_file}",
        f"Reason: {reason}",
    ]
    if parsed_output:
        lines.extend(["Agent output:", *parsed_output])
    else:
        lines.append("Agent output: (no output captured)")
    return "\n".join(lines)


def _collect_commit_agent_output(
    lines: Iterable[object],
    *,
    parser_type: str,
    agent_name: str,
    verbose: bool,
) -> list[str]:
    parser = _resolve_commit_parser(parser_type)
    parsed_output: list[str] = []
    try:
        for parsed_line in parser.parse(str(line) for line in lines):
            rendered = _render_commit_agent_activity_line(parsed_line, agent_name)
            if rendered is None:
                continue
            parsed_output.append(rendered)
            if verbose:
                console.print(rendered)
    except AgentInvocationError as exc:
        raise _invocation_error_with_output(exc, parsed_output) from exc
    return parsed_output


def _resolve_commit_parser(parser_type: str) -> AgentParser:
    try:
        return get_parser(parser_type)
    except ValueError:
        return get_parser("generic")


def _render_commit_agent_activity_line(output: AgentOutputLine, agent_name: str) -> str | None:
    if output.type == "text":
        content = output.content.strip()
        return f"[white]{agent_name}:[/white] {content}" if content else None
    if output.type == "tool_use":
        tool_name = output.content.strip() or "unknown-tool"
        summary = _tool_input_summary(output.metadata)
        return f"[magenta]{agent_name} tool:[/magenta] {tool_name}" + (
            f" ({summary})" if summary else ""
        )
    if output.type == "tool_result":
        result = output.content.strip()
        if result:
            return f"[dim]{agent_name} tool result:[/dim] {result}"
        summary = _event_summary(output)
        return f"[dim]{agent_name} tool result:[/dim] {summary}" if summary else None
    if output.type == "error":
        error = output.content.strip() or "unknown error"
        return f"[red]{agent_name} error:[/red] {error}"
    summary = _event_summary(output)
    return f"[dim]{agent_name} {output.type}:[/dim] {summary}"


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
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str, sort_keys=True)
    return ""


def _invocation_error_with_output(
    exc: AgentInvocationError,
    parsed_output: list[str],
) -> AgentInvocationError:
    return AgentInvocationError(
        exc.agent_name,
        exc.returncode,
        exc.stderr,
        parsed_output=list(parsed_output),
    )


def _parsed_output_from_invocation_error(exc: AgentInvocationError) -> list[str]:
    parsed_output: list[str] = exc.parsed_output
    return parsed_output


def _handle_show_or_generate(
    repo_root: Path,
    generate: bool,
    apply: bool,
    git_user_name: str | None,
    git_user_email: str | None,
) -> None:
    """Handle commit message generation and display.

    Args:
        repo_root: Repository root path.
        generate: Whether to generate commit message.
        apply: Whether to apply (commit) the changes.
        git_user_name: Git user name for commit.
        git_user_email: Git user email for commit.
    """
    staged_files = get_staged_files(repo_root)

    if not staged_files:
        console.print("[yellow]No staged files[/yellow]")
        return

    console.print(f"[cyan]Staged files:[/cyan] {len(staged_files)}")
    for f in staged_files[:_MAX_DISPLAY_FILES]:
        console.print(f"  - {f}")
    if len(staged_files) > _MAX_DISPLAY_FILES:
        console.print(f"  ... and {len(staged_files) - _MAX_DISPLAY_FILES} more")

    if generate:
        # Generate commit message
        message = _generate_commit_message(staged_files, repo_root)
        console.print("\n[green]Generated commit message:[/green]")
        console.print(Panel(message, border_style="green"))

        if apply:
            try:
                sha = create_commit(
                    repo_root,
                    message,
                    author_name=git_user_name,
                    author_email=git_user_email,
                )
                delete_commit_message_artifacts(repo_root)
                console.print(f"\n[green]Created commit:[/green] {sha[:8]}")
            except Exception as e:
                console.print(f"\n[red]Commit failed:[/red] {e}")


def _generate_commit_message(files: list[str], repo_root: Path) -> str:
    """Generate a commit message from staged files.

    Args:
        files: List of staged file paths.
        repo_root: Repository root path.

    Returns:
        Generated commit message.
    """
    # Simple heuristic commit message generation
    # In a real implementation, this would invoke an agent

    if not files:
        return "Update files"

    # Group files by type
    added: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []

    for f in files:
        if f.startswith("src/"):
            added.append(f)
        elif f.startswith("tests/"):
            modified.append(f)
        else:
            added.append(f)

    parts: list[str] = []

    if added:
        count = len(added)
        parts.append(f"Update {count} file{'s' if count > 1 else ''}")

    if modified:
        count = len(modified)
        parts.append(f"Modify {count} file{'s' if count > 1 else ''}")

    if deleted:
        count = len(deleted)
        parts.append(f"Remove {count} file{'s' if count > 1 else ''}")

    if not parts:
        parts = ["Update files"]

    return ": ".join(parts)


def _start_commit_bridge(repo_root: Path) -> SessionBridgeLike:
    session = AgentSession(
        session_id=f"commit-{uuid.uuid4().hex[:8]}",
        run_id=str(uuid.uuid4()),
        drain="commit",
        capabilities={
            "ArtifactSubmit",
            "RunReportProgress",
            "WorkspaceRead",
            "WorkspaceWriteEphemeral",
        },
    )
    workspace = FsWorkspace(repo_root)
    return start_mcp_server(session, workspace)


def _commit_bridge_env(bridge: SessionBridgeLike) -> dict[str, str]:
    return {
        MCP_ENDPOINT_ENV: bridge.agent_endpoint_uri(),
        MCP_RUN_ID_ENV: "commit-plumbing",
    }
