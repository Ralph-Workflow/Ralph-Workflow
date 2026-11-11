
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class ProjectAgentSkillRoot:

    agent: str
    path_segments: tuple[str, ...]
    source_url: str = ""
    is_canonical: bool = False
    last_verified_iso: str = ""

    def resolve(self, workspace_root: Path) -> Path:
        return workspace_root.joinpath(*self.path_segments)


__all__ = ["ProjectAgentSkillRoot"]
