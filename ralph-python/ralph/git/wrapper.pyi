from __future__ import annotations

from pathlib import Path
from typing import Optional


class GitHelpers:
    real_git: Optional[Path]
    wrapper_dir: Optional[Path]
    wrapper_repo_root: Optional[Path]

    def __init__(self) -> None: ...


def start_agent_phase(repo_root: Path | str, helpers: Optional[GitHelpers] = ...) -> None: ...


def end_agent_phase(repo_root: Path | str, helpers: Optional[GitHelpers] = ...) -> None: ...


def detect_unauthorized_commit(repo_root: Path | str) -> bool: ...
