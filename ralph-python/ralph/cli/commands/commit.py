"""Commit plumbing commands for Ralph CLI.

This module implements commit-related commands for generating
and applying commit messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from git import Repo
from rich.console import Console
from rich.panel import Panel

from ralph.agents.invoke import AgentInvocationError, InvokeOptions, invoke_agent
from ralph.agents.parsers import get_parser
from ralph.agents.registry import AgentRegistry
from ralph.config.loader import load_config
from ralph.git.operations import (
    create_commit,
    find_repo_root,
    get_staged_files,
    has_staged_changes,
    stage_all,
)
from ralph.prompts.commit import prompt_commit_message
from ralph.prompts.template_registry import TemplateRegistry, default_template_dirs

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import AgentConfig, UnifiedConfig

console = Console()

# Maximum number of staged files to display in output
_MAX_DISPLAY_FILES = 5
_DEFAULT_COMMIT_AGENT = "claude"
_VERBOSE_THRESHOLD = 2
_COMMIT_MESSAGE_FILE = ".agent/tmp/commit-message.txt"
_SKIP_PREFIX = "skip:"


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

    message, skipped = _generate_commit_message_with_chain(
        diff=diff,
        repo_root=repo_root,
        registry=registry,
        agents=agents,
        verbose=config.general.verbosity >= _VERBOSE_THRESHOLD,
    )

    if skipped:
        _delete_commit_message_file(repo_root)
        console.print("[yellow]Skipping commit: agent requested skip[/yellow]")
        return

    if not message:
        console.print("[red]Failed to generate commit message from commit drain agents[/red]")
        return

    _write_commit_message_file(repo_root, message)

    console.print("\n[green]Generated commit message:[/green]")
    console.print(Panel(message, border_style="green"))

    if apply:
        stage_all(repo_root)
        try:
            sha = create_commit(
                repo_root,
                message,
                author_name=git_user_name,
                author_email=git_user_email,
            )
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


def _generate_commit_message_with_chain(
    *,
    diff: str,
    repo_root: Path,
    registry: AgentRegistry,
    agents: list[str],
    verbose: bool,
) -> tuple[str, bool]:
    template_dirs = (repo_root / ".agent" / "prompts" / "commit", *default_template_dirs(repo_root))
    template_registry = TemplateRegistry(template_dirs=template_dirs)
    prompt = prompt_commit_message(diff, template_registry=template_registry)
    prompt_file = _write_commit_prompt_file(repo_root, prompt)

    for agent_name in agents:
        cfg = registry.get(agent_name)
        if cfg is None:
            continue
        try:
            message, skipped = _generate_commit_message_with_agent(
                cfg,
                prompt_file=prompt_file,
                verbose=verbose,
            )
        except AgentInvocationError:
            continue

        if skipped:
            return "", True
        if message:
            return message, False

    return "", False


def _generate_commit_message_with_agent(
    agent: AgentConfig,
    *,
    prompt_file: str,
    verbose: bool,
) -> tuple[str, bool]:
    lines = invoke_agent(agent, prompt_file, options=InvokeOptions(verbose=verbose))
    parser = get_parser(str(agent.json_parser))
    text_parts = [
        line.content for line in parser.parse(str(raw) for raw in lines) if line.type == "text"
    ]
    full_text = "\n".join(part for part in text_parts if part).strip()
    if not full_text:
        return "", False

    first_line = next((line.strip() for line in full_text.splitlines() if line.strip()), "")
    if _is_skip_response(first_line):
        return "", True

    return first_line, False


def _is_skip_response(text: str) -> bool:
    return text.strip().lower().startswith(_SKIP_PREFIX)


def _write_commit_prompt_file(repo_root: Path, prompt: str) -> str:
    prompt_path = repo_root / ".agent" / "tmp" / "commit_prompt.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt, encoding="utf-8")
    return str(prompt_path)


def _commit_message_path(repo_root: Path) -> Path:
    return repo_root / _COMMIT_MESSAGE_FILE


def _write_commit_message_file(repo_root: Path, message: str) -> None:
    commit_message_file = _commit_message_path(repo_root)
    commit_message_file.parent.mkdir(parents=True, exist_ok=True)
    commit_message_file.write_text(message, encoding="utf-8")


def _read_commit_message_file(repo_root: Path) -> str | None:
    commit_message_file = _commit_message_path(repo_root)
    if not commit_message_file.exists():
        return None
    contents = commit_message_file.read_text(encoding="utf-8").strip()
    if not contents:
        return None
    return contents


def _delete_commit_message_file(repo_root: Path) -> None:
    commit_message_file = _commit_message_path(repo_root)
    if commit_message_file.exists():
        commit_message_file.unlink()


def _show_commit_message(repo_root: Path) -> None:
    commit_message = _read_commit_message_file(repo_root)
    if commit_message is None:
        console.print("[red]No commit message generated yet[/red]")
        return

    console.print("\n[green]Commit message:[/green]")
    console.print(Panel(commit_message, border_style="green"))


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
