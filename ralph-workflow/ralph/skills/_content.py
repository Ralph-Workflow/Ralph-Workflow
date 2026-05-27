"""Baseline skill content access."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import TYPE_CHECKING, TypedDict, cast

if TYPE_CHECKING:
    from pathlib import Path


class SkillMetadata(TypedDict):
    source_repo: str
    source_commit: str
    source_version: str
    source_repos: list[str]
    mirrored_at: str
    skills: list[str]
    bundles: dict[str, str]
    skill_sources: dict[str, dict[str, object]]


_MANAGED_MARKER = ".ralph-managed.json"


def _read_skill_metadata() -> SkillMetadata:
    content_dir = files(__package__) / "content"
    raw = (content_dir / "metadata.json").read_text(encoding="utf-8")
    return cast("SkillMetadata", json.loads(raw))


BASELINE_SKILL_NAMES: tuple[str, ...] = tuple(_read_skill_metadata()["skills"])


def list_skill_names() -> tuple[str, ...]:
    return BASELINE_SKILL_NAMES


def get_skill_content(name: str) -> str:
    if name not in BASELINE_SKILL_NAMES:
        msg = f"Unknown baseline skill: {name}"
        raise ValueError(msg)
    content_dir = files(__package__) / "content"
    return (content_dir / f"{name}.md").read_text(encoding="utf-8")


def get_skill_metadata() -> SkillMetadata:
    return _read_skill_metadata()


def managed_skill_marker(name: str) -> dict[str, str]:
    return {
        "managed_by": "ralph-workflow",
        "skill": name,
    }


def materialize_skills_to_dir(target: Path) -> list[str]:
    target.mkdir(parents=True, exist_ok=True)
    written_names: list[str] = []
    metadata = get_skill_metadata()
    for name in BASELINE_SKILL_NAMES:
        (target / f"{name}.md").write_text(get_skill_content(name), encoding="utf-8")
        written_names.append(name)
    (target / "metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )
    return written_names


def materialize_skills_to_claude_dir(target: Path) -> list[str]:
    target.mkdir(parents=True, exist_ok=True)
    written_names: list[str] = []
    metadata = get_skill_metadata()
    for name in BASELINE_SKILL_NAMES:
        skill_dir = target / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(get_skill_content(name), encoding="utf-8")
        (skill_dir / _MANAGED_MARKER).write_text(
            json.dumps(managed_skill_marker(name), indent=2) + "\n",
            encoding="utf-8",
        )
        written_names.append(name)
    (target / "metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )
    return written_names


__all__ = [
    "BASELINE_SKILL_NAMES",
    "_MANAGED_MARKER",
    "get_skill_content",
    "get_skill_metadata",
    "list_skill_names",
    "managed_skill_marker",
    "materialize_skills_to_claude_dir",
    "materialize_skills_to_dir",
]
