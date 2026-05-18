from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class CommitPlumbingOptions:
    """Options for commit plumbing operations."""

    generate_commit_msg: bool = False
    generate_commit: bool = False
    show_commit_msg: bool = False
    config_path: Path | None = None
    cli_overrides: dict[str, object] | None = None
