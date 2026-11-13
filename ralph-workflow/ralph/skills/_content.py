from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.skills._ownership import Ownership, classify_entry

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.artifacts.file_backend import FileBackend


_MANAGED_MARKER = ".ralph-managed.json"


_CONTENT_DIR = files("ralph.skills") / "content"

BASELINE_SKILL_NAMES: tuple[str, ...] = tuple(
    sorted(
        path.name.removesuffix(".md")
        for path in _CONTENT_DIR.iterdir()
        if path.is_file() and path.name.endswith(".md")
    )
)


def _metadata_payload() -> dict[str, list[str]]:
    return {"skills": list(BASELINE_SKILL_NAMES)}


def list_skill_names() -> tuple[str, ...]:
    return BASELINE_SKILL_NAMES


def get_skill_content(name: str) -> str:
    if name not in BASELINE_SKILL_NAMES:
        msg = f"Unknown baseline skill: {name}"
        raise ValueError(msg)
    return (_CONTENT_DIR / f"{name}.md").read_text(encoding="utf-8")


def managed_skill_marker(name: str, *, installed_sha256: str) -> dict[str, str]:
    return {
        "managed_by": "ralph-workflow",
        "skill": name,
        "installed_content_sha256": installed_sha256,
    }


def materialize_skills_to_dir(
    target: Path,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> list[str]:
    target.mkdir(parents=True, exist_ok=True)
    written_names: list[str] = []
    metadata = _metadata_payload()
    for name in BASELINE_SKILL_NAMES:
        write_text_if_changed(
            backend,
            target / f"{name}.md",
            get_skill_content(name),
            encoding="utf-8",
        )
        written_names.append(name)
    write_text_if_changed(
        backend,
        target / "metadata.json",
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )
    return written_names


def _may_materialize_skill_dir(skill_dir: Path) -> bool:
    if skill_dir.is_symlink():
        return False
    ownership = classify_entry(skill_dir)
    return ownership in (Ownership.ABSENT, Ownership.MANAGED)


def materialize_skills_to_claude_dir(
    target: Path,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> list[str]:
    target.mkdir(parents=True, exist_ok=True)
    written_names: list[str] = []
    metadata = _metadata_payload()
    for name in BASELINE_SKILL_NAMES:
        skill_dir = target / name
        if not _may_materialize_skill_dir(skill_dir):
            continue
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        marker_file = skill_dir / _MANAGED_MARKER
        content = get_skill_content(name)
        write_text_if_changed(backend, skill_file, content, encoding="utf-8")
        content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        write_text_if_changed(
            backend,
            marker_file,
            json.dumps(managed_skill_marker(name, installed_sha256=content_sha), indent=2) + "\n",
            encoding="utf-8",
        )
        written_names.append(name)
    write_text_if_changed(
        backend,
        target / "metadata.json",
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )
    return written_names


__all__ = [
    "BASELINE_SKILL_NAMES",
    "_MANAGED_MARKER",
    "get_skill_content",
    "list_skill_names",
    "managed_skill_marker",
    "materialize_skills_to_claude_dir",
    "materialize_skills_to_dir",
]
