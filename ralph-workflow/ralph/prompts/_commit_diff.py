from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.executor.process import ProcessRunOptions, run_process
from ralph.git.commit_review import is_recognized_secret_path

if TYPE_CHECKING:
    from pathlib import Path
from ralph.prompts.payload_refs import sanitize_surrogates as _sanitize_surrogates


def _is_inside_git_repo(workspace_root: Path) -> bool:
    path = workspace_root.resolve()
    for _ in range(100):
        if (path / ".git").exists():
            return True
        parent = path.parent
        if parent == path:
            return False
        path = parent
    return False


_MAX_UNTRACKED_FILES_IN_DIFF: int = 500

_UNTRACKED_HEADER: str = "## Untracked files (not yet tracked by git):"

_UNTRACKED_FOOTER_TEMPLATE: str = (
    "... and {remaining} more untracked files not shown (see git status)"
)

_NO_DIFF_SENTINEL: str = "(no diff available)"
_REDACTED_SECRET_CHANGES: str = (
    "## Recognized secret changes\n"
    "[redacted: secret files will be removed from version control before commit]"
)


def _git_output_or_empty(workspace_root: Path, *args: str) -> str:
    result = run_process(
        "git",
        args,
        options=ProcessRunOptions(cwd=workspace_root),
    )
    if result.returncode != 0:
        return ""
    return _sanitize_surrogates(result.stdout).strip()


def _format_untracked_section(untracked_paths: list[str]) -> str:
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
    untracked_paths = [line for line in untracked_raw.splitlines() if line.strip()]
    untracked_section = _format_untracked_section(untracked_paths)
    if not tracked:
        return untracked_section
    if not untracked_section:
        return tracked
    return f"{tracked}\n\n{untracked_section}"


def _untracked_only_pending_work(workspace_root: Path) -> tuple[str, bool] | None:
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
    if not secret_work_present:
        return diff
    if not diff:
        return _REDACTED_SECRET_CHANGES
    return f"{diff}\n\n{_REDACTED_SECRET_CHANGES}"


def commit_generation_diff(workspace_root: Path) -> str:
    diff, secret_work_present = _secret_filtered_pending_diff(workspace_root)
    return _with_secret_signal(diff, secret_work_present)


def commit_review_diff(workspace_root: Path, proposal_ref: str | None = None) -> str:
    if proposal_ref and _is_inside_git_repo(workspace_root):
        ref_check = run_process(
            "git",
            ("rev-parse", "--verify", proposal_ref),
            options=ProcessRunOptions(cwd=workspace_root),
        )
        if ref_check.returncode == 0:
            head_check = run_process(
                "git",
                ("rev-parse", "--verify", "HEAD"),
                options=ProcessRunOptions(cwd=workspace_root),
            )
            tracked_paths_raw = _git_output_or_empty(workspace_root, "ls-files", "--cached", "-z")
            tracked_secret_paths = [
                path
                for path in tracked_paths_raw.split("\0")
                if path and is_recognized_secret_path(path)
            ]
            diff_base: tuple[str, ...] = (
                ("diff", "HEAD", proposal_ref)
                if head_check.returncode == 0
                else ("diff", proposal_ref)
            )
            diff_args: tuple[str, ...] = (
                *diff_base,
                "--",
                ".",
                *(f":(exclude,literal){path}" for path in tracked_secret_paths),
            )
            tracked = _git_output_or_empty(workspace_root, *diff_args)
            if tracked:
                return tracked
    diff, secret_work_present = _secret_filtered_pending_diff(workspace_root)
    return _with_secret_signal(diff, secret_work_present) or _NO_DIFF_SENTINEL


__all__ = ["commit_generation_diff", "commit_review_diff"]
