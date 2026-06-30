"""Baseline skill content access.

Reads bundled skill Markdown and metadata from ``ralph.skills.content``
and materializes it into user-global or per-agent skill directories.
Preserves user edits when the installed content hash no longer matches
the managed marker.
"""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from typing import TYPE_CHECKING, TypedDict, cast

if TYPE_CHECKING:
    from pathlib import Path


class SkillMetadata(TypedDict):
    """JSON shape of ``metadata.json`` shipped with the bundled skills."""

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
    """Load the bundled skill metadata from package resources."""
    content_dir = files(__package__) / "content"
    raw = (content_dir / "metadata.json").read_text(encoding="utf-8")
    return cast("SkillMetadata", json.loads(raw))


BASELINE_SKILL_NAMES: tuple[str, ...] = tuple(_read_skill_metadata()["skills"])


def list_skill_names() -> tuple[str, ...]:
    """Return the names of all bundled baseline skills."""
    return BASELINE_SKILL_NAMES


def get_skill_content(name: str) -> str:
    """Return the Markdown content for a bundled skill.

    Raises:
        ValueError: If ``name`` is not a bundled baseline skill.
    """
    if name not in BASELINE_SKILL_NAMES:
        msg = f"Unknown baseline skill: {name}"
        raise ValueError(msg)
    content_dir = files(__package__) / "content"
    return (content_dir / f"{name}.md").read_text(encoding="utf-8")


def get_skill_metadata() -> SkillMetadata:
    """Return the bundled skill metadata dict."""
    return _read_skill_metadata()


def managed_skill_marker(name: str, *, installed_sha256: str) -> dict[str, str]:
    """Build the contents of a ``.ralph-managed.json`` marker file."""
    return {
        "managed_by": "ralph-workflow",
        "skill": name,
        "installed_content_sha256": installed_sha256,
    }


def materialize_skills_to_dir(target: Path) -> list[str]:
    """Write every bundled skill as ``target/{name}.md`` plus metadata.

    Returns:
        The list of skill names written.
    """
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
    """Write bundled skills into Claude-style ``target/{name}/SKILL.md`` dirs.

    Skips a skill when an existing ``SKILL.md`` and ``.ralph-managed.json``
    marker indicate the user manually edited the file. Returns the names of
    skills actually written or refreshed.

    Args:
        target: Root skill directory (e.g. ``~/.claude/skills``).

    Returns:
        The list of skill names materialized.
    """
    target.mkdir(parents=True, exist_ok=True)
    written_names: list[str] = []
    metadata = get_skill_metadata()
    for name in BASELINE_SKILL_NAMES:
        skill_dir = target / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        marker_file = skill_dir / _MANAGED_MARKER
        if skill_file.exists() and marker_file.exists():
            stored_sha: str = ""
            try:
                marker_data = cast(
                    "dict[str, object]",
                    json.loads(marker_file.read_text(encoding="utf-8")),
                )
                sha_val = marker_data.get("installed_content_sha256", "")
                stored_sha = sha_val if isinstance(sha_val, str) else ""
            except Exception:
                pass
            if stored_sha:
                current_sha = hashlib.sha256(skill_file.read_bytes()).hexdigest()
                if current_sha != stored_sha:
                    continue  # user manually edited; preserve
        content = get_skill_content(name)
        skill_file.write_text(content, encoding="utf-8")
        content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        marker_file.write_text(
            json.dumps(managed_skill_marker(name, installed_sha256=content_sha), indent=2) + "\n",
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
