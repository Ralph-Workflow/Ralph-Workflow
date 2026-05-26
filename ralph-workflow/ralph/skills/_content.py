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


__all__ = [
    "BASELINE_SKILL_NAMES",
    "get_skill_content",
    "get_skill_metadata",
    "list_skill_names",
    "materialize_skills_to_dir",
]
