from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from tests._scenario_agent_mode import AgentMode
from tests._scenario_git import git

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution import RebaseStop

__all__ = ["FakeAgentRunner"]


@dataclass
class FakeAgentRunner:
    mode: AgentMode
    launches: int = 0

    def __call__(self, root: Path, _target: str, stop: RebaseStop) -> bool:
        self.launches += 1
        if self.mode is AgentMode.LEAVES_MARKER:
            return True
        for relative in stop.conflicted_files:
            path = root / relative
            if path.is_file():
                path.write_text("resolved by scenario agent\n", encoding="utf-8")
        if self.mode is AgentMode.EDITS_AND_STAGES:
            git(root, "add", "--", *stop.conflicted_files)
        return True
