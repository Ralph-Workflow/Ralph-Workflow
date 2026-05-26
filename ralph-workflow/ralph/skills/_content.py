"""Baseline skill content access."""

from __future__ import annotations

from importlib.resources import files
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

BASELINE_SKILL_NAMES: tuple[str, ...] = (
    "using-superpowers",
    "brainstorming",
    "writing-plans",
    "executing-plans",
    "subagent-driven-development",
    "dispatching-parallel-agents",
    "test-driven-development",
    "systematic-debugging",
    "requesting-code-review",
    "receiving-code-review",
    "verification-before-completion",
    "finishing-a-development-branch",
    "using-git-worktrees",
    "writing-skills",
    "security-review",
    "verification-loop",
    "coding-standards",
)


def list_skill_names() -> tuple[str, ...]:
    return BASELINE_SKILL_NAMES


def get_skill_content(name: str) -> str:
    if name not in BASELINE_SKILL_NAMES:
        msg = f"Unknown baseline skill: {name}"
        raise ValueError(msg)
    content_dir = files(__package__) / "content"
    return (content_dir / f"{name}.md").read_text(encoding="utf-8")


def materialize_skills_to_dir(target: Path) -> list[str]:
    target.mkdir(parents=True, exist_ok=True)
    written_names: list[str] = []
    for name in BASELINE_SKILL_NAMES:
        (target / f"{name}.md").write_text(get_skill_content(name), encoding="utf-8")
        written_names.append(name)
    return written_names


__all__ = [
    "BASELINE_SKILL_NAMES",
    "get_skill_content",
    "list_skill_names",
    "materialize_skills_to_dir",
]
