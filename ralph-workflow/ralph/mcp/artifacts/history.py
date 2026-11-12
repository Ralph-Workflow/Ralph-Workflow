from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.handoffs import handoff_path_for_artifact
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


_HISTORY_SUBDIR = "history"
_TIMESTAMP_PATTERN = re.compile(r"^(\d{8}T\d{6})_")
_TIMESTAMP_LENGTH = 15
HISTORY_RETENTION_LIMIT = 64


def history_dir_for_artifact(artifact_dir: Path, artifact_type: str) -> Path:
    return artifact_dir / _HISTORY_SUBDIR / artifact_type


def history_index_path(artifact_dir: Path, artifact_type: str) -> Path:
    return history_dir_for_artifact(artifact_dir, artifact_type) / "index.md"


def _safe_timestamp(now_iso: Callable[[], str]) -> str:
    raw = now_iso()
    compact = raw.replace("-", "").replace(":", "").replace("+", "").replace(".", "")
    return compact[:_TIMESTAMP_LENGTH] if len(compact) >= _TIMESTAMP_LENGTH else compact


def _unique_archive_path(
    hist_dir: Path,
    artifact_type: str,
    suffix: str,
    *,
    backend: FileBackend,
    now_iso: Callable[[], str],
) -> Path:
    base = _safe_timestamp(now_iso)
    candidate = hist_dir / f"{base}_{artifact_type}{suffix}"
    counter = 1
    while backend.exists(candidate):
        candidate = hist_dir / f"{base}_{counter}_{artifact_type}{suffix}"
        counter += 1
    return candidate


def archive_artifact_before_overwrite(
    artifact_dir: Path,
    workspace_root: Path,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    now_iso: Callable[[], str],
) -> list[Path]:
    canonical_markdown = artifact_dir / f"{artifact_type}.md"
    if not backend.exists(canonical_markdown):
        return []

    hist_dir = history_dir_for_artifact(artifact_dir, artifact_type)
    backend.mkdir(hist_dir, parents=True, exist_ok=True)

    created: list[Path] = []

    archive_markdown = _unique_archive_path(
        hist_dir,
        artifact_type,
        ".md",
        backend=backend,
        now_iso=now_iso,
    )
    write_text_if_changed(backend, archive_markdown, backend.read_text(canonical_markdown))
    created.append(archive_markdown)

    handoff_rel = handoff_path_for_artifact(artifact_type)
    if handoff_rel:
        handoff_abs = workspace_root / handoff_rel
        if backend.exists(handoff_abs):
            archive_md = _unique_archive_path(
                hist_dir,
                artifact_type,
                ".md",
                backend=backend,
                now_iso=now_iso,
            )
            write_text_if_changed(backend, archive_md, backend.read_text(handoff_abs))
            created.append(archive_md)

    prune_artifact_history(artifact_dir, artifact_type, backend=backend)
    rebuild_history_index(artifact_dir, artifact_type, backend=backend)

    return created


def snapshot_current_artifact(
    artifact_dir: Path,
    workspace_root: Path,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    now_iso: Callable[[], str],
) -> list[Path]:
    canonical_markdown = artifact_dir / f"{artifact_type}.md"
    if not backend.exists(canonical_markdown):
        return []

    hist_dir = history_dir_for_artifact(artifact_dir, artifact_type)
    backend.mkdir(hist_dir, parents=True, exist_ok=True)
    created: list[Path] = []

    archive_markdown = _unique_archive_path(
        hist_dir,
        artifact_type,
        ".md",
        backend=backend,
        now_iso=now_iso,
    )
    write_text_if_changed(backend, archive_markdown, backend.read_text(canonical_markdown))
    created.append(archive_markdown)

    handoff_rel = handoff_path_for_artifact(artifact_type)
    if handoff_rel:
        handoff_abs = workspace_root / handoff_rel
        if backend.exists(handoff_abs):
            archive_md = _unique_archive_path(
                hist_dir,
                artifact_type,
                ".md",
                backend=backend,
                now_iso=now_iso,
            )
            write_text_if_changed(backend, archive_md, backend.read_text(handoff_abs))
            created.append(archive_md)

    prune_artifact_history(artifact_dir, artifact_type, backend=backend)
    rebuild_history_index(artifact_dir, artifact_type, backend=backend)
    return created


def prune_artifact_history(
    artifact_dir: Path,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    limit: int = HISTORY_RETENTION_LIMIT,
) -> list[Path]:
    if limit < 0:
        raise ValueError("history retention limit must be non-negative")
    hist_dir = history_dir_for_artifact(artifact_dir, artifact_type)
    if not backend.exists(hist_dir):
        return []
    archives = sorted(p for p in backend.glob(hist_dir, "*.md") if _TIMESTAMP_PATTERN.match(p.name))
    removed = archives[:-limit] if limit else archives
    for path in removed:
        backend.unlink(path)
    return removed


def rebuild_history_index(
    artifact_dir: Path,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    hist_dir = history_dir_for_artifact(artifact_dir, artifact_type)
    if not backend.exists(hist_dir):
        return

    markdown_files = sorted(
        p for p in backend.glob(hist_dir, "*.md") if _TIMESTAMP_PATTERN.match(p.name)
    )

    if not markdown_files:
        index_path = hist_dir / "index.md"
        if backend.exists(index_path):
            backend.unlink(index_path)
        return

    lines = [f"# Artifact History: {artifact_type}", ""]
    lines.append(
        "Prior versions of this artifact are archived below, oldest first. "
        "Each entry shows the timestamp and archived markdown source document."
    )
    lines.append("")

    for markdown_path in markdown_files:
        m = _TIMESTAMP_PATTERN.match(markdown_path.name)
        ts = m.group(1) if m else markdown_path.stem
        lines.append(f"## {ts}")
        lines.append("")
        lines.append(f"- Markdown: `{markdown_path.name}`")
        lines.append("")

    index_path = hist_dir / "index.md"
    write_text_if_changed(backend, index_path, "\n".join(lines))


def clear_artifact_history(
    artifact_dir: Path,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    hist_dir = history_dir_for_artifact(artifact_dir, artifact_type)
    if not backend.exists(hist_dir):
        return

    for path in list(backend.glob(hist_dir, "*.md")):
        backend.unlink(path, missing_ok=True)


__all__ = [
    "archive_artifact_before_overwrite",
    "clear_artifact_history",
    "history_dir_for_artifact",
    "history_index_path",
    "prune_artifact_history",
    "rebuild_history_index",
    "snapshot_current_artifact",
]
