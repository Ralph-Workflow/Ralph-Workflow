
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import atomic_write_text_if_changed, write_text_if_changed
from ralph.mcp.artifacts.plan._size_limits import PlanSizeLimits

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.artifacts.markdown import MdArtifactSpec

DEFAULT_MD_DRAFT_CHARACTER_CAP: int = PlanSizeLimits.DEFAULT.max_total_bytes


@dataclass(frozen=True)
class UnsubmittedDraftDivergence:

    draft_chars: int
    canonical_chars: int


def unsubmitted_draft_divergence(
    artifact_dir: Path,
    artifact_type: str,
    canonical_path: Path,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> UnsubmittedDraftDivergence | None:
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
    if spec.max_characters is not None:
        return spec.max_characters
    return DEFAULT_MD_DRAFT_CHARACTER_CAP


def md_draft_filename(artifact_type: str) -> str:
    return f".{artifact_type}.draft.md"


def md_draft_path(artifact_dir: Path, artifact_type: str) -> Path:
    return artifact_dir / md_draft_filename(artifact_type)


def _md_draft_revision_path(artifact_dir: Path, artifact_type: str) -> Path:
    return artifact_dir / f".{artifact_type}.draft.revision"


def load_md_draft_revision(
    artifact_dir: Path, artifact_type: str, *, backend: FileBackend = DEFAULT_FILE_BACKEND
) -> int:
    try:
        return int(
            backend.read_text(
                _md_draft_revision_path(artifact_dir, artifact_type), encoding="utf-8"
            )
        )
    except (KeyError, OSError, ValueError):
        return 0


def _seeded_draft_path(artifact_dir: Path, artifact_type: str) -> Path:
    return artifact_dir / f".{artifact_type}.draft.seeded"


def md_draft_workspace_path(artifact_type: str) -> str:
    return f".agent/artifacts/{md_draft_filename(artifact_type)}"


def seeded_draft_workspace_path(artifact_type: str) -> str:
    return f".agent/artifacts/.{artifact_type}.draft.seeded"


def load_md_draft(
    artifact_dir: Path,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> str | None:
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
    seeded_path = _seeded_draft_path(artifact_dir, artifact_type)
    if changed:
        write_text_if_changed(
            backend,
            _md_draft_revision_path(artifact_dir, artifact_type),
            str(load_md_draft_revision(artifact_dir, artifact_type, backend=backend) + 1),
            encoding="utf-8",
        )
        if backend.exists(seeded_path):
            backend.unlink(seeded_path)


def mark_md_draft_seeded(
    artifact_dir: Path,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
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
    return backend.exists(_seeded_draft_path(artifact_dir, artifact_type))


def delete_md_draft(
    artifact_dir: Path,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> bool:
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
    "load_md_draft_revision",
    "mark_md_draft_seeded",
    "md_draft_character_cap",
    "md_draft_path",
    "save_md_draft",
    "seeded_draft_workspace_path",
    "unsubmitted_draft_divergence",
]
