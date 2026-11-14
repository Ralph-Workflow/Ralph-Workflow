from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.skills._agent_paths import (
    agent_skill_roots,
    project_sibling_skill_roots,
    project_skill_root,
)
from ralph.skills.upstream import UNIVERSAL_SKILL_NAMES

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

_ENV_VAR = "RALPH_INLINE_SKILLS_DIR"

FROZEN_UNIVERSAL_SKILLS: tuple[str, ...] = UNIVERSAL_SKILL_NAMES


def get_inline_skill_content() -> str:
    raw_dir = os.environ.get(_ENV_VAR)
    if not raw_dir:
        return ""

    skill_dir = Path(raw_dir)
    if not skill_dir.is_dir():
        return ""

    chunks: list[str] = []
    for md_file in sorted(skill_dir.glob("*.md")):
        content = md_file.read_text(encoding="utf-8").strip()
        if content:
            chunks.append(content)
    return "\n\n---\n\n".join(chunks)


def skill_variable_name(skill_name: str) -> str:
    return f"SKILL_INSTALLED_{skill_name.replace('-', '_').upper()}"


def skill_template_variables(
    installed_skills: Iterable[str] | None = None,
    candidate_skills: Sequence[str] = FROZEN_UNIVERSAL_SKILLS,
) -> dict[str, str]:
    installed_set = set(installed_skills) if installed_skills is not None else set()
    return {
        skill_variable_name(skill): ("true" if skill in installed_set else "")
        for skill in candidate_skills
    }


def resolve_agent_skill_roots(
    agent: str | None,
    *,
    workspace_root: Path | None = None,
) -> tuple[Path, ...]:
    if not agent:
        return ()
    agent_key = agent.lower().strip()
    roots: list[Path] = []
    for entry in agent_skill_roots():
        entry_agent = entry.agent.lower()
        if (
            entry_agent == agent_key
            or entry_agent.startswith(f"{agent_key}-")
            or agent_key.startswith(f"{entry_agent}-")
            or agent_key.startswith(f"{entry_agent}_")
            or entry_agent == agent_key.split("-")[0]
            or entry_agent == agent_key.split("_")[0]
        ):
            roots.append(entry.resolve())

    if workspace_root is not None:
        try:
            for p_entry in project_sibling_skill_roots(workspace_root):
                entry_agent = p_entry.agent.lower()
                if (
                    entry_agent == agent_key
                    or entry_agent.startswith(f"{agent_key}-")
                    or agent_key.startswith(f"{entry_agent}-")
                    or agent_key.startswith(f"{entry_agent}_")
                    or entry_agent == agent_key.split("-")[0]
                    or entry_agent == agent_key.split("_")[0]
                ):
                    roots.append(p_entry.resolve(workspace_root))
            if agent_key == "opencode" or agent_key.startswith("opencode"):
                roots.append(project_skill_root(workspace_root))
        except Exception:
            pass

    seen: set[Path] = set()
    unique_roots: list[Path] = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            unique_roots.append(r)
    return tuple(unique_roots)


def _is_skill_installed(skill_dir: Path) -> bool:
    try:
        resolved = skill_dir.resolve()
        return resolved.is_dir() and (resolved / "SKILL.md").is_file()
    except (OSError, RuntimeError):
        return False


def resolve_installed_skills_for_agent(
    agent: str | None,
    candidate_skills: Sequence[str] | None = None,
    *,
    workspace_root: Path | None = None,
    roots: Sequence[Path] | None = None,
) -> set[str]:
    if not agent and roots is None:
        return set()
    candidates = candidate_skills if candidate_skills is not None else FROZEN_UNIVERSAL_SKILLS
    if not candidates:
        return set()

    search_roots = (
        tuple(roots)
        if roots is not None
        else resolve_agent_skill_roots(agent, workspace_root=workspace_root)
    )
    if not search_roots:
        return set()

    installed: set[str] = set()
    for skill in candidates:
        for root in search_roots:
            skill_dir = root / skill
            if _is_skill_installed(skill_dir):
                installed.add(skill)
                break
    return installed


resolve_installed_skills = resolve_installed_skills_for_agent

__all__ = [
    "FROZEN_UNIVERSAL_SKILLS",
    "get_inline_skill_content",
    "resolve_agent_skill_roots",
    "resolve_installed_skills",
    "resolve_installed_skills_for_agent",
    "skill_template_variables",
    "skill_variable_name",
]
