from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ralph.skills._ownership import UNIVERSAL_MANAGED_BY, read_managed_marker
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


AGENT_FAMILIES: Final[dict[str, str]] = {
    "claude": "claude",
    "codex": "codex",
    "codex-agents": "codex",
    "opencode": "opencode",
    "agy": "agy",
    "agy-config": "agy",
}


def agent_family_roots(agent: str) -> tuple[AgentSkillRoot, ...]:
    family = AGENT_FAMILIES.get(agent, agent)
    return tuple(
        entry
        for entry in AGENT_SKILL_ROOTS
        if AGENT_FAMILIES.get(entry.agent, entry.agent) == family
    )


def alternate_agent_roots(agent: str) -> tuple[AgentSkillRoot, ...]:
    return tuple(entry for entry in agent_family_roots(agent) if entry.agent != agent)


def agent_skill_entries(agent: str, *, home: Path | None = None) -> tuple[Path, ...]:
    base = home if home is not None else Path.home()
    entries: list[Path] = []
    for entry in agent_family_roots(agent):
        root = base.joinpath(*entry.path_segments)
        if not root.is_dir():
            continue
        try:
            for child in sorted(root.iterdir()):
                if (child.is_dir() or child.is_symlink()) and not child.name.startswith("."):
                    entries.append(child)
        except OSError:
            continue
    return tuple(entries)


def agent_skill_presence_set(agent: str, *, home: Path | None = None) -> frozenset[str]:
    return frozenset(
        entry.name
        for entry in agent_skill_entries(agent, home=home)
        if not is_ralph_managed_skill(entry)
    )


def is_ralph_managed_skill(skill_dir: Path) -> bool:
    marker = read_managed_marker(skill_dir)
    return marker is not None and marker.get("managed_by") == UNIVERSAL_MANAGED_BY


def is_skill_present_for_agent(skill_name: str, agent: str, *, home: Path | None = None) -> bool:
    return skill_name in agent_skill_presence_set(agent, home=home)


def is_skill_present_in_alternate_root(skill_name: str, target_root: Path) -> bool:
    parts = target_root.parts
    matched_entry: AgentSkillRoot | None = None
    for entry in AGENT_SKILL_ROOTS:
        n = len(entry.path_segments)
        if len(parts) >= n and parts[-n:] == entry.path_segments:
            matched_entry = entry
            break
    if matched_entry is None:
        resolved_target = target_root.resolve()
        for entry in AGENT_SKILL_ROOTS:
            if entry.resolve().resolve() == resolved_target:
                matched_entry = entry
                break
    if matched_entry is None:
        return False
    n = len(matched_entry.path_segments)
    base = target_root.parents[n - 1] if len(parts) >= n else Path.home()
    for alt in alternate_agent_roots(matched_entry.agent):
        alt_root = base.joinpath(*alt.path_segments)
        candidate = alt_root / skill_name
        if not (candidate.exists() or candidate.is_symlink()):
            continue
        if not is_ralph_managed_skill(candidate):
            return True
    return False


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
    "AGENT_FAMILIES",
    "AGENT_SKILL_ROOTS",
    "_SKILL_ROOT_PREFIXES",
    "AgentSkillRoot",
    "ProjectAgentSkillRoot",
    "agent_family_roots",
    "agent_skill_entries",
    "agent_skill_presence_set",
    "agent_skill_roots",
    "alternate_agent_roots",
    "canonical_agent_skill_root",
    "is_ralph_managed_skill",
    "is_skill_present_for_agent",
    "is_skill_present_in_alternate_root",
    "project_sibling_skill_roots",
    "project_skill_root",
    "sibling_agent_skill_roots",
]
