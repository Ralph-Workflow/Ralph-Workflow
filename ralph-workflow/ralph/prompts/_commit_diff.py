"""Workspace diff helpers for commit cleanup prompt rendering."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.executor.process import ProcessRunOptions, run_process

if TYPE_CHECKING:
    from pathlib import Path
from ralph.prompts.payload_refs import sanitize_surrogates as _sanitize_surrogates


def _is_inside_git_repo(workspace_root: Path) -> bool:
    """Fast path: detect a git working tree by walking up for ``.git``.

    This avoids spawning ``git`` processes for workspaces that are not under
    version control (common in unit tests using ``tmp_path``). It correctly
    handles repositories nested inside other repositories and stops at the
    filesystem root.
    """
    path = workspace_root.resolve()
    for _ in range(100):
        if (path / ".git").exists():
            return True
        parent = path.parent
        if parent == path:
            return False
        path = parent
    return False


def _git_output_if_repo(workspace_root: Path, *args: str) -> str:
    """Run a git command only when the workspace is inside a git working tree."""
    if not _is_inside_git_repo(workspace_root):
        return _NO_DIFF_SENTINEL
    return _git_output(workspace_root, *args)


# Maximum number of untracked file paths to surface in the cleanup diff before
# the list is truncated. Keeps the prompt size bounded and prevents prompt
# overflow when a workspace contains huge numbers of untracked files
# (e.g. a node_modules tree that escaped the .gitignore).
_MAX_UNTRACKED_FILES_IN_DIFF: int = 500

# Shared header used by the commit cleanup diff and the commit phase diff to
# mark the untracked file list section. Importing this constant from
# ``materialize`` keeps both call sites in sync.
_UNTRACKED_HEADER: str = "## Untracked files (not yet tracked by git):"

# Truncation footer shown when the untracked file list is capped. The literal
# value is intentionally stable so the prompt-renderer tests can grep for it.
_UNTRACKED_FOOTER_TEMPLATE: str = (
    "... and {remaining} more untracked files not shown (see git status)"
)

_NO_DIFF_SENTINEL: str = "(no diff available)"


def _git_output(workspace_root: Path, *args: str) -> str:
    result = run_process(
        "git",
        args,
        options=ProcessRunOptions(cwd=workspace_root),
    )
    if result.returncode != 0:
        return _NO_DIFF_SENTINEL
    return _sanitize_surrogates(result.stdout).strip() or _NO_DIFF_SENTINEL


def _format_untracked_section(untracked_paths: list[str]) -> str:
    """Return the untracked-files section, capped with a truncation footer.

    Args:
        untracked_paths: Untracked file paths relative to the workspace root,
            in the order returned by ``git ls-files --others --exclude-standard``.

    Returns:
        The header line followed by at most ``_MAX_UNTRACKED_FILES_IN_DIFF``
        paths and an optional ``... and N more`` footer.
    """
    if not untracked_paths:
        return ""
    total = len(untracked_paths)
    if total <= _MAX_UNTRACKED_FILES_IN_DIFF:
        body = "\n".join(untracked_paths)
        return f"{_UNTRACKED_HEADER}\n{body}"
    visible = untracked_paths[:_MAX_UNTRACKED_FILES_IN_DIFF]
    remaining = total - _MAX_UNTRACKED_FILES_IN_DIFF
    footer = _UNTRACKED_FOOTER_TEMPLATE.format(remaining=remaining)
    return f"{_UNTRACKED_HEADER}\n" + "\n".join(visible) + f"\n{footer}"


def commit_cleanup_diff(workspace_root: Path) -> str:
    """Return the pending diff for commit cleanup prompt rendering.

    Combines the tracked ``git diff HEAD`` output with the untracked file list
    (``git ls-files --others --exclude-standard``). The untracked list is
    capped at ``_MAX_UNTRACKED_FILES_IN_DIFF`` entries and a truncation
    footer is appended when more files exist.
    """
    tracked = _git_output_if_repo(workspace_root, "diff", "HEAD")
    untracked_raw = _git_output_if_repo(
        workspace_root, "ls-files", "--others", "--exclude-standard"
    )
    if untracked_raw == _NO_DIFF_SENTINEL:
        untracked_paths: list[str] = []
    else:
        untracked_paths = [
            line for line in untracked_raw.splitlines() if line.strip()
        ]
    untracked_section = _format_untracked_section(untracked_paths)
    if not untracked_section:
        return tracked
    if tracked == _NO_DIFF_SENTINEL:
        return untracked_section
    return f"{tracked}\n\n{untracked_section}"
