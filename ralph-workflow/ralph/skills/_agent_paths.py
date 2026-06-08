"""Canonical user-global skill-discovery roots for every supported agent.

SCOPE: this registry covers the USER-GLOBAL skill root for each agent
(typically under $HOME). It does NOT cover project-local roots such as
./.opencode/skills/ or ./.claude/skills/. Project-local skill discovery
varies by agent and by cwd; intentionally out of scope for the initial
implementation. Future maintainers who want to add project-local roots
must add an explicit test that documents the new invariant.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# A skill-discovery root is the directory an agent scans for `skills/<name>/SKILL.md`.
# Sources (research-confirmed at plan time):
#   - Claude Code:    ~/.claude/skills/<name>/SKILL.md
#                     https://docs.claude.com/claude-code (skill loading rules)
#   - Codex:          $CODEX_HOME/skills/<name>/SKILL.md  (default ~/.codex/skills/)
#                     https://github.com/openai/codex (codex-rs/utils/home-dir/src/lib.rs)
#   - OpenCode:       ~/.config/opencode/skills/<name>/SKILL.md
#                     https://github.com/anomalyco/opencode/blob/dev/packages/web/src/content/docs/skills.mdx
#                     NOTE: OpenCode ALSO scans ~/.claude/skills/ and ~/.agents/skills/ (covered
#                     by the Claude install) AND a project-local ./.opencode/skills/ root
#                     (OUT OF SCOPE for this registry).
#   - AGY (Antigravity CLI): ~/.gemini/antigravity-cli/skills/<name>/SKILL.md
#                     https://medium.com/google-cloud/configuring-mcp-servers-and-skills-for-antigravity-cli-and-ide-a938c7eebb78
#   - Nanocoder:      no skills concept; uses commands under .nanocoder/commands/ — OUT OF SCOPE.


@dataclass(frozen=True)
class AgentSkillRoot:
    agent: str
    # e.g. (".claude", "skills") -- joined onto Path.home() at call time
    path_segments: tuple[str, ...]
    source_url: str  # authoritative doc URL for this mapping
    is_canonical: bool  # True for the single root we materialize; sibling roots get symlinks

    def resolve(self) -> Path:
        """Build the absolute path under the current Path.home().

        Called every time, so test code that monkeypatches Path.home re-resolves
        these paths correctly. NEVER cache the resolved Path on the dataclass.
        """
        return Path.home().joinpath(*self.path_segments)


# Canonical (materialize-here) root must come first so installers can use it as the
# symlink target. Sibling roots after it.
AGENT_SKILL_ROOTS: tuple[AgentSkillRoot, ...] = (
    AgentSkillRoot(
        agent="claude",
        path_segments=(".claude", "skills"),
        source_url="https://docs.claude.com/claude-code",
        is_canonical=True,
    ),
    AgentSkillRoot(
        agent="codex",
        path_segments=(".codex", "skills"),
        source_url="https://github.com/openai/codex/blob/main/codex-rs/utils/home-dir/src/lib.rs",
        is_canonical=False,
    ),
    AgentSkillRoot(
        agent="opencode",
        path_segments=(".config", "opencode", "skills"),
        source_url="https://github.com/anomalyco/opencode/blob/dev/packages/web/src/content/docs/skills.mdx",
        is_canonical=False,
    ),
    AgentSkillRoot(
        agent="agy",
        path_segments=(".gemini", "antigravity-cli", "skills"),
        source_url="https://medium.com/google-cloud/configuring-mcp-servers-and-skills-for-antigravity-cli-and-ide-a938c7eebb78",
        is_canonical=False,
    ),
)


def agent_skill_roots() -> tuple[AgentSkillRoot, ...]:
    """Return the registered user-global AgentSkillRoot entries."""
    return AGENT_SKILL_ROOTS


def canonical_agent_skill_root() -> AgentSkillRoot:
    for entry in AGENT_SKILL_ROOTS:
        if entry.is_canonical:
            return entry
    msg = "No canonical AgentSkillRoot registered"
    raise RuntimeError(msg)


def sibling_agent_skill_roots() -> tuple[AgentSkillRoot, ...]:
    return tuple(entry for entry in AGENT_SKILL_ROOTS if not entry.is_canonical)


__all__ = [
    "AGENT_SKILL_ROOTS",
    "AgentSkillRoot",
    "agent_skill_roots",
    "canonical_agent_skill_root",
    "sibling_agent_skill_roots",
]
