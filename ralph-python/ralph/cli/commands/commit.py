"""Commit plumbing commands for Ralph CLI.

This module implements commit-related commands for generating
and applying commit messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel

from ralph.config.loader import load_config
from ralph.git.operations import (
    create_commit,
    find_repo_root,
    get_staged_files,
    has_staged_changes,
    is_repo_clean,
    stage_all,
)

if TYPE_CHECKING:
    from pathlib import Path

console = Console()

# Maximum number of staged files to display in output
_MAX_DISPLAY_FILES = 5


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

    # Get git user info from config
    git_user_name = config.general.git_user_name
    git_user_email = config.general.git_user_email

    # Check for staged changes
    if not has_staged_changes(repo_root) and opts.generate_commit and not is_repo_clean(repo_root):
        stage_all(repo_root)

    if not has_staged_changes(repo_root):
        console.print("[yellow]No staged changes to commit[/yellow]")
        return

    if opts.generate_commit_msg or opts.generate_commit or opts.show_commit_msg:
        _handle_show_or_generate(
            repo_root=repo_root,
            generate=opts.generate_commit_msg or opts.generate_commit,
            apply=opts.generate_commit,
            git_user_name=git_user_name,
            git_user_email=git_user_email,
        )


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
