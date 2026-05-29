"""Workspace diff helpers for commit cleanup prompt rendering."""

from __future__ import annotations

from pathlib import Path

from ralph.executor.process import ProcessRunOptions, run_process
from ralph.prompts.payload_refs import sanitize_surrogates as _sanitize_surrogates


def _git_output(workspace_root: Path, *args: str) -> str:
    result = run_process(
        "git",
        args,
        options=ProcessRunOptions(cwd=workspace_root),
    )
    if result.returncode != 0:
        return "(no diff available)"
    return _sanitize_surrogates(result.stdout).strip() or "(no diff available)"


def commit_cleanup_diff(workspace_root: Path) -> str:
    """Return the pending diff for commit cleanup prompt rendering."""
    return _git_output(workspace_root, "diff", "HEAD")
