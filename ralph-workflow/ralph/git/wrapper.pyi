from __future__ import annotations

from pathlib import Path

from ralph.git.git_command_queue import WorktreeLease

class GitHelpers:
    real_git: Path | None
    wrapper_dir: Path | None
    wrapper_repo_root: Path | None
    worktree_lease: WorktreeLease | None

    def __init__(self) -> None: ...

def start_agent_phase(repo_root: Path | str, helpers: GitHelpers | None = ...) -> None: ...
def end_agent_phase(repo_root: Path | str, helpers: GitHelpers | None = ...) -> None: ...
def detect_unauthorized_commit(repo_root: Path | str) -> bool: ...
