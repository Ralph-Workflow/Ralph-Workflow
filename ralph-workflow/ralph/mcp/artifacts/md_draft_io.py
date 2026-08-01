"""On-disk persistence for staged markdown artifact drafts.

A draft is one plain markdown file per artifact type at
``<artifact_dir>/.<artifact_type>.draft.md`` that accumulates staged
content during incremental authoring. Reads and writes go through a
``FileBackend`` so production code can be unit-tested with an in-memory
backend, and the draft survives an MCP server restart (resumability).
Writes are atomic (temp write + replace).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import atomic_write_text_if_changed, write_text_if_changed
from ralph.mcp.artifacts.plan._size_limits import PlanSizeLimits

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.artifacts.markdown import MdArtifactSpec

#: Fallback character cap for artifact types whose markdown spec does not
#: pin ``max_characters``. Matches ``PlanSizeLimits.DEFAULT.max_total_bytes``
#: (the 4 MB artifact payload cap) so staging cannot outgrow submission.
DEFAULT_MD_DRAFT_CHARACTER_CAP: int = PlanSizeLimits.DEFAULT.max_total_bytes


@dataclass(frozen=True)
class UnsubmittedDraftDivergence:
    """A retained draft differs from the last canonically submitted document."""

    draft_chars: int
    canonical_chars: int


def unsubmitted_draft_divergence(
    artifact_dir: Path,
    artifact_type: str,
    canonical_path: Path,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> UnsubmittedDraftDivergence | None:
    """Return divergence when authored draft content was never submitted.

    Missing drafts and canonical files are handled by their existing completion
    gates, so this comparison only reports a readable draft ahead of a readable
    canonical document.
    """
    draft = load_md_draft(artifact_dir, artifact_type, backend=backend)
    if draft is None or not backend.exists(canonical_path):
        return None
    try:
        canonical = backend.read_text(canonical_path, encoding="utf-8")
    except OSError:
        return None
    if draft == canonical:
        return None
    return UnsubmittedDraftDivergence(len(draft), len(canonical))


def md_draft_character_cap(spec: MdArtifactSpec) -> int:
    """Return the staging cap: the spec's own cap when set, else the 4 MB default."""
    if spec.max_characters is not None:
        return spec.max_characters
    return DEFAULT_MD_DRAFT_CHARACTER_CAP


def md_draft_filename(artifact_type: str) -> str:
    """Return the draft file name for one artifact type."""
    return f".{artifact_type}.draft.md"


def md_draft_path(artifact_dir: Path, artifact_type: str) -> Path:
    """Return the canonical draft path for one artifact type."""
    return artifact_dir / md_draft_filename(artifact_type)


def _seeded_draft_path(artifact_dir: Path, artifact_type: str) -> Path:
    """Return the private marker that identifies a canonical-seeded draft."""
    return artifact_dir / f".{artifact_type}.draft.seeded"


def md_draft_workspace_path(artifact_type: str) -> str:
    """Return the workspace-relative draft path for one artifact type."""
    return f".agent/artifacts/{md_draft_filename(artifact_type)}"


def seeded_draft_workspace_path(artifact_type: str) -> str:
    """Return the workspace-relative provenance marker for a seeded draft."""
    return f".agent/artifacts/.{artifact_type}.draft.seeded"


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
    draft_path = md_draft_path(artifact_dir, artifact_type)
    tmp_path = draft_path.with_suffix(".md.tmp")
    changed = atomic_write_text_if_changed(
        backend,
        draft_path,
        content,
        tmp_path=tmp_path,
        encoding="utf-8",
        sync_directory=True,
        prepare_write=lambda: backend.mkdir(artifact_dir, parents=True, exist_ok=True),
    )
    # An identical replay did not author a new draft. Preserve seeded provenance
    # and avoid its otherwise redundant deletion mutation; a changed publication
    # becomes authored content and must clear the marker as before.
    seeded_path = _seeded_draft_path(artifact_dir, artifact_type)
    if changed and backend.exists(seeded_path):
        backend.unlink(seeded_path)


def mark_md_draft_seeded(
    artifact_dir: Path,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Mark a draft copied from canonical content rather than authored incrementally."""
    write_text_if_changed(
        backend,
        _seeded_draft_path(artifact_dir, artifact_type),
        "",
        encoding="utf-8",
    )


def is_md_draft_seeded(
    artifact_dir: Path,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> bool:
    """Return whether a draft was reconstructed from canonical content."""
    return backend.exists(_seeded_draft_path(artifact_dir, artifact_type))


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
    seeded_path = _seeded_draft_path(artifact_dir, artifact_type)
    if backend.exists(seeded_path):
        backend.unlink(seeded_path)
    return True


__all__ = [
    "DEFAULT_MD_DRAFT_CHARACTER_CAP",
    "delete_md_draft",
    "is_md_draft_seeded",
    "load_md_draft",
    "mark_md_draft_seeded",
    "md_draft_character_cap",
    "md_draft_path",
    "save_md_draft",
    "seeded_draft_workspace_path",
    "unsubmitted_draft_divergence",
]
