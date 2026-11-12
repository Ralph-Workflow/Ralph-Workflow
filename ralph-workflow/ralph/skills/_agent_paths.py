from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ralph.skills._project_paths import ProjectAgentSkillRoot


@dataclass(frozen=True)
class AgentSkillRoot:
    agent: str
    path_segments: tuple[str, ...]
    source_url: str
    is_canonical: bool
    last_verified_iso: str = ""

    def resolve(self) -> Path:
        return Path.home().joinpath(*self.path_segments)


_AGENT_PATH_LAST_VERIFIED = "2026-11-11"

AGENT_SKILL_ROOTS: tuple[AgentSkillRoot, ...] = (
    AgentSkillRoot(
        agent="claude",
        path_segments=(".claude", "skills"),
        source_url="https://code.claude.com/docs/en/skills",
        is_canonical=True,
        last_verified_iso=_AGENT_PATH_LAST_VERIFIED,
    ),
    AgentSkillRoot(
        agent="codex",
        path_segments=(".codex", "skills"),
        source_url=(
            "https://github.com/openai/codex/blob/main/"
            "codex-rs/skills/src/assets/samples/skill-installer/SKILL.md"
        ),
        is_canonical=False,
        last_verified_iso=_AGENT_PATH_LAST_VERIFIED,
    ),
    AgentSkillRoot(
        agent="opencode",
        path_segments=(".config", "opencode", "skills"),
        source_url="https://opencode.ai/docs/skills/",
        is_canonical=False,
        last_verified_iso=_AGENT_PATH_LAST_VERIFIED,
    ),
    AgentSkillRoot(
        agent="agy",
        path_segments=(".gemini", "antigravity-cli", "skills"),
        source_url="https://antigravity.google/docs/skills/",
        is_canonical=False,
        last_verified_iso=_AGENT_PATH_LAST_VERIFIED,
    ),
    AgentSkillRoot(
        agent="codex-agents",
        path_segments=(".agents", "skills"),
        source_url="https://learn.chatgpt.com/docs/build-skills",
        is_canonical=False,
        last_verified_iso=_AGENT_PATH_LAST_VERIFIED,
    ),
    AgentSkillRoot(
        agent="agy-config",
        path_segments=(".gemini", "config", "skills"),
        source_url="https://antigravity.google/docs/skills/",
        is_canonical=False,
        last_verified_iso=_AGENT_PATH_LAST_VERIFIED,
    ),
)


def agent_skill_roots() -> tuple[AgentSkillRoot, ...]:
    return AGENT_SKILL_ROOTS


def canonical_agent_skill_root() -> AgentSkillRoot:
    for entry in AGENT_SKILL_ROOTS:
        if entry.is_canonical:
            return entry
    msg = "No canonical AgentSkillRoot registered"
    raise RuntimeError(msg)


def sibling_agent_skill_roots() -> tuple[AgentSkillRoot, ...]:
    return tuple(entry for entry in AGENT_SKILL_ROOTS if not entry.is_canonical)


_PROJECT_CANONICAL_DIR_SEGMENTS: tuple[str, ...] = (".opencode", "skills")

_SKILL_ROOT_PREFIXES: frozenset[str] = frozenset(
    {
        ".opencode/skills/",
        ".agents/skills/",
        ".claude/skills/",
        ".codex/skills/",
        ".gemini/antigravity-cli/skills/",
    }
)


def project_skill_root(workspace_root: Path) -> Path:
    return workspace_root.joinpath(*_PROJECT_CANONICAL_DIR_SEGMENTS)


def project_sibling_skill_roots(
    workspace_root: Path,
) -> tuple[ProjectAgentSkillRoot, ...]:
    _ = workspace_root
    by_agent: dict[str, AgentSkillRoot] = {entry.agent: entry for entry in AGENT_SKILL_ROOTS}
    pi_source_url = "https://pi.dev/docs/latest/usage"
    pi_last_verified_iso = _AGENT_PATH_LAST_VERIFIED
    agents: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
        (
            "claude",
            (".claude", "skills"),
            by_agent["claude"].source_url,
            by_agent["claude"].last_verified_iso,
        ),
        (
            "codex",
            (".codex", "skills"),
            by_agent["codex"].source_url,
            by_agent["codex"].last_verified_iso,
        ),
        (
            "agy",
            (".gemini", "antigravity-cli", "skills"),
            by_agent["agy"].source_url,
            by_agent["agy"].last_verified_iso,
        ),
        ("pi", (".agents", "skills"), pi_source_url, pi_last_verified_iso),
    )
    return tuple(
        ProjectAgentSkillRoot(
            agent=agent,
            path_segments=segments,
            source_url=source_url,
            is_canonical=False,
            last_verified_iso=last_verified_iso,
        )
        for agent, segments, source_url, last_verified_iso in agents
    )


__all__ = [
    "AGENT_SKILL_ROOTS",
    "_SKILL_ROOT_PREFIXES",
    "AgentSkillRoot",
    "ProjectAgentSkillRoot",
    "agent_skill_roots",
    "canonical_agent_skill_root",
    "project_sibling_skill_roots",
    "project_skill_root",
    "sibling_agent_skill_roots",
]
