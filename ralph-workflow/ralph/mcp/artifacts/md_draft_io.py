"""On-disk persistence for staged markdown artifact drafts.

A draft is one plain markdown file per artifact type at
``<artifact_dir>/.<artifact_type>.draft.md`` that accumulates staged
content during incremental authoring. Reads and writes go through a
``FileBackend`` so production code can be unit-tested with an in-memory
backend, and the draft survives an MCP server restart (resumability).
Writes are atomic (temp write + replace).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.plan import PlanSizeLimits

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.artifacts.markdown import MdArtifactSpec

#: Fallback character cap for artifact types whose markdown spec does not
#: pin ``max_characters``. Matches ``PlanSizeLimits.DEFAULT.max_total_bytes``
#: (the 4 MB artifact payload cap) so staging cannot outgrow submission.
DEFAULT_MD_DRAFT_CHARACTER_CAP: int = PlanSizeLimits.DEFAULT.max_total_bytes


def md_draft_character_cap(spec: MdArtifactSpec) -> int:
    """Return the staging cap: the spec's own cap when set, else the 4 MB default."""
    if spec.max_characters is not None:
        return spec.max_characters
    return DEFAULT_MD_DRAFT_CHARACTER_CAP


def md_draft_path(artifact_dir: Path, artifact_type: str) -> Path:
    """Return the canonical draft path for one artifact type."""
    return artifact_dir / f".{artifact_type}.draft.md"


def load_md_draft(
    artifact_dir: Path,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> str | None:
    """Read the staged draft if present and readable. None otherwise."""
    draft_path = md_draft_path(artifact_dir, artifact_type)
    if not backend.exists(draft_path):
        return None
    try:
        return backend.read_text(draft_path, encoding="utf-8")
    except OSError:
        return None


def save_md_draft(
    artifact_dir: Path,
    artifact_type: str,
    content: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Atomically persist the staged draft (temp write + replace)."""
    backend.mkdir(artifact_dir, parents=True, exist_ok=True)
    draft_path = md_draft_path(artifact_dir, artifact_type)
    tmp_path = draft_path.with_suffix(".md.tmp")
    backend.write_text(tmp_path, content, encoding="utf-8")
    backend.replace(tmp_path, draft_path)


def delete_md_draft(
    artifact_dir: Path,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> bool:
    """Remove the staged draft. Returns True if one existed."""
    draft_path = md_draft_path(artifact_dir, artifact_type)
    if not backend.exists(draft_path):
        return False
    backend.unlink(draft_path)
    return True


__all__ = [
    "DEFAULT_MD_DRAFT_CHARACTER_CAP",
    "delete_md_draft",
    "load_md_draft",
    "md_draft_character_cap",
    "md_draft_path",
    "save_md_draft",
]
