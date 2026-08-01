"""Workspace diff helpers for direct commit generation and cleanup prompts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.executor.process import ProcessRunOptions, run_process
from ralph.git.commit_cleanup import is_recognized_secret_path

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
_REDACTED_SECRET_CHANGES: str = (
    "## Recognized secret changes\n"
    "[redacted: secret files will be removed from version control before commit]"
)


def _git_output_or_empty(workspace_root: Path, *args: str) -> str:
    """Return sanitized git stdout, or an empty string on failure/no output."""
    result = run_process(
        "git",
        args,
        options=ProcessRunOptions(cwd=workspace_root),
    )
    if result.returncode != 0:
        return ""
    return _sanitize_surrogates(result.stdout).strip()


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


def _combine_tracked_and_untracked(tracked: str, untracked_raw: str) -> str:
    """Combine a tracked diff with the bounded untracked-path section."""
    untracked_paths = [line for line in untracked_raw.splitlines() if line.strip()]
    untracked_section = _format_untracked_section(untracked_paths)
    if not tracked:
        return untracked_section
    if not untracked_section:
        return tracked
    return f"{tracked}\n\n{untracked_section}"


def _untracked_only_pending_work(workspace_root: Path) -> tuple[str, bool] | None:
    """Return safe untracked work, or ``None`` when tracked work needs a diff.

    ``git status`` answers the common cleanup case in one process.  A tracked
    entry falls through to the full diff path, which is needed to include its
    contents; an untracked-only tree needs only the bounded path list.
    """
    status = _git_output_or_empty(workspace_root, "status", "--porcelain=v1", "-z")
    if not status:
        return None
    entries = [entry for entry in status.split("\0") if entry]
    if any(not entry.startswith("?? ") for entry in entries):
        return None
    untracked_paths = [entry[3:] for entry in entries]
    safe_untracked = [path for path in untracked_paths if not is_recognized_secret_path(path)]
    return _format_untracked_section(safe_untracked), any(
        is_recognized_secret_path(path) for path in untracked_paths
    )


def _secret_filtered_pending_diff(workspace_root: Path) -> tuple[str, bool]:
    """Return safe pending work and whether recognized secret work was hidden."""
    if not _is_inside_git_repo(workspace_root):
        return "", False
    untracked_only = _untracked_only_pending_work(workspace_root)
    if untracked_only is not None:
        return untracked_only
    head_check = run_process(
        "git",
        ("rev-parse", "--verify", "HEAD"),
        options=ProcessRunOptions(cwd=workspace_root),
    )
    tracked_paths_raw = _git_output_or_empty(workspace_root, "ls-files", "--cached", "-z")
    tracked_secret_paths = [
        path for path in tracked_paths_raw.split("\0") if path and is_recognized_secret_path(path)
    ]
    tracked_base = ("diff", "HEAD") if head_check.returncode == 0 else ("diff", "--cached")
    tracked_args = (
        *tracked_base,
        "--",
        ".",
        *(f":(exclude,literal){path}" for path in tracked_secret_paths),
    )
    tracked = _git_output_or_empty(workspace_root, *tracked_args)
    tracked_secret_changes_present = False
    if tracked_secret_paths:
        secret_diff = run_process(
            "git",
            (
                *tracked_base,
                "--quiet",
                "--",
                *(f":(literal){path}" for path in tracked_secret_paths),
            ),
            options=ProcessRunOptions(cwd=workspace_root),
        )
        tracked_secret_changes_present = secret_diff.returncode == 1
    untracked_raw = _git_output_or_empty(
        workspace_root,
        "ls-files",
        "--others",
        "--exclude-standard",
    )
    safe_untracked = "\n".join(
        path for path in untracked_raw.splitlines() if path and not is_recognized_secret_path(path)
    )
    combined = _combine_tracked_and_untracked(tracked, safe_untracked)
    untracked_secret_present = any(
        path and is_recognized_secret_path(path) for path in untracked_raw.splitlines()
    )
    return combined, tracked_secret_changes_present or untracked_secret_present


def _with_secret_signal(diff: str, secret_work_present: bool) -> str:
    """Append a content-free remediation signal when secret work was hidden."""
    if not secret_work_present:
        return diff
    if not diff:
        return _REDACTED_SECRET_CHANGES
    return f"{diff}\n\n{_REDACTED_SECRET_CHANGES}"


def commit_generation_diff(workspace_root: Path) -> str:
    """Return safe pending work that a direct commit message must describe."""
    diff, secret_work_present = _secret_filtered_pending_diff(workspace_root)
    return _with_secret_signal(diff, secret_work_present)


def commit_cleanup_diff(workspace_root: Path) -> str:
    """Return safe pending work and a content-free secret cleanup signal."""
    diff, secret_work_present = _secret_filtered_pending_diff(workspace_root)
    return _with_secret_signal(diff, secret_work_present) or _NO_DIFF_SENTINEL


__all__ = ["commit_cleanup_diff", "commit_generation_diff"]
