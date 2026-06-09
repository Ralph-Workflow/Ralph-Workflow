"""Project-scope skill discovery root dataclass and helpers.

Project-scope roots are computed at call time from an explicit
``workspace_root`` so the same dataclass can be reused across the
project canonical fan-out and the per-agent sibling mirror under
``./.claude/skills/``, ``./.codex/skills/``, ``./.gemini/antigravity-cli/skills/``.

This file is intentionally SEPARATE from ``_agent_paths.py`` so the
single-class-per-file repo policy holds. The two scopes (user-global
``AgentSkillRoot`` and project-scope ``ProjectAgentSkillRoot``) are
deliberately NOT the same dataclass because their ``resolve()``
signatures differ (one takes ``Path.home()`` implicitly, the other
takes an explicit ``workspace_root`` argument) and merging them would
introduce a runtime branch inside a frozen dataclass.

SCOPE-DOWN RATIONALE (PA-007): only the 3 non-canonical USER-GLOBAL
entries (claude, codex, agy) appear as project siblings. The opencode
project sibling is intentionally absent because ``./.opencode/skills/``
IS the project canonical (would be a self-symlink), and the user-global
opencode root at ``~/.config/opencode/skills/`` is already covered by
the canonical user-global install. See ``_agent_paths.py`` MAINTENANCE
for the full rationale.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class ProjectAgentSkillRoot:
    """Project-scope mirror of :class:`ralph.skills._agent_paths.AgentSkillRoot`.

    Carries the same research contract (``source_url`` +
    ``last_verified_iso``) as the user-global ``AgentSkillRoot`` so the
    supported-agent registry is audited for BOTH scopes. The path
    segments are joined onto an explicit ``workspace_root`` at
    ``resolve()`` time — this dataclass is NEVER resolved against
    ``Path.home()`` and never appears in ``AGENT_SKILL_ROOTS``.
    """

    agent: str
    path_segments: tuple[str, ...]
    source_url: str = ""
    is_canonical: bool = False
    last_verified_iso: str = ""

    def resolve(self, workspace_root: Path) -> Path:
        """Build the absolute path under the given workspace_root.

        Called every time so test code that passes a per-test tmp_path
        re-resolves these paths correctly. NEVER cache the resolved Path
        on the dataclass.
        """
        return workspace_root.joinpath(*self.path_segments)


__all__ = ["ProjectAgentSkillRoot"]
