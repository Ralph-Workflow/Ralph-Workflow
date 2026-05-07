"""Artifact history archival and indexing.

When a phase's artifact_history policy has enabled=True, the runtime archives
the current canonical artifact JSON and its Markdown handoff into a stable
history directory before overwriting them. This lets planning agents inspect
prior failed plans and analysis decisions across re-planning loops.

Layout under .agent/artifacts/:
    history/<artifact_type>/            -- history root for a type
        <timestamp>_<artifact_type>.json  -- archived canonical JSON
        <timestamp>_<artifact_type>.md    -- archived Markdown handoff (when present)
        index.md                          -- human-readable summary of archived entries

The canonical latest files (.agent/artifacts/plan.json, .agent/PLAN.md, etc.)
are never moved here — they remain the authoritative current state. History
contains only prior versions that were overwritten.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


_HISTORY_SUBDIR = "history"
_TIMESTAMP_PATTERN = re.compile(r"^(\d{8}T\d{6})_")
_TIMESTAMP_LENGTH = 15


def history_dir_for_artifact(artifact_dir: Path, artifact_type: str) -> Path:
    """Return the history directory for an artifact type."""
    return artifact_dir / _HISTORY_SUBDIR / artifact_type


def history_index_path(artifact_dir: Path, artifact_type: str) -> Path:
    """Return the path to the history index file for an artifact type."""
    return history_dir_for_artifact(artifact_dir, artifact_type) / "index.md"


def _safe_timestamp(now_iso: Callable[[], str]) -> str:
    """Return a filesystem-safe timestamp from an ISO string."""
    raw = now_iso()
    # Convert "2026-04-15T12:00:00+00:00" -> "20260415T120000"
    compact = raw.replace("-", "").replace(":", "").replace("+", "").replace(".", "")
    # Keep only the 15-char date-time prefix: YYYYMMDDTHHMMSS
    return compact[:_TIMESTAMP_LENGTH] if len(compact) >= _TIMESTAMP_LENGTH else compact


def archive_artifact_before_overwrite(
    artifact_dir: Path,
    workspace_root: Path,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    now_iso: Callable[[], str],
) -> list[Path]:
    """Archive the current canonical artifact files before they are overwritten.

    Reads the current canonical JSON artifact and its Markdown handoff (if any),
    writes them into the history directory under a timestamped prefix, then
    rebuilds the history index.

    Args:
        artifact_dir: The artifacts directory (e.g. .agent/artifacts/).
        workspace_root: Workspace root (used to locate Markdown handoff files).
        artifact_type: The artifact type identifier (e.g. 'plan').
        backend: File backend for I/O.
        now_iso: Callable returning the current timestamp as an ISO 8601 string.

    Returns:
        List of Paths of files created by this operation (JSON and MD archives,
        NOT the index). The caller can use these paths to roll back the archive.
    """
    canonical_json = artifact_dir / f"{artifact_type}.json"
    if not backend.exists(canonical_json):
        return []

    timestamp = _safe_timestamp(now_iso)
    hist_dir = history_dir_for_artifact(artifact_dir, artifact_type)
    backend.mkdir(hist_dir, parents=True, exist_ok=True)

    created: list[Path] = []

    # Archive the canonical JSON
    archive_json = hist_dir / f"{timestamp}_{artifact_type}.json"
    backend.write_text(archive_json, backend.read_text(canonical_json))
    created.append(archive_json)

    # Archive the Markdown handoff if it exists
    from ralph.mcp.artifacts.handoffs import handoff_path_for_artifact  # noqa: PLC0415

    handoff_rel = handoff_path_for_artifact(artifact_type)
    if handoff_rel:
        handoff_abs = workspace_root / handoff_rel
        if backend.exists(handoff_abs):
            archive_md = hist_dir / f"{timestamp}_{artifact_type}.md"
            backend.write_text(archive_md, backend.read_text(handoff_abs))
            created.append(archive_md)

    # Rebuild the index to include the new entry
    rebuild_history_index(artifact_dir, artifact_type, backend=backend)

    return created


def rebuild_history_index(
    artifact_dir: Path,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Rebuild the history index from files present in the history directory.

    Always writes a fresh index.md derived from directory contents so the index
    stays consistent with the actual archived files regardless of rollback state.
    """
    hist_dir = history_dir_for_artifact(artifact_dir, artifact_type)
    if not backend.exists(hist_dir):
        return

    json_files = sorted(
        p for p in backend.glob(hist_dir, "*.json") if _TIMESTAMP_PATTERN.match(p.name)
    )

    if not json_files:
        index_path = hist_dir / "index.md"
        if backend.exists(index_path):
            backend.unlink(index_path)
        return

    lines = [f"# Artifact History: {artifact_type}", ""]
    lines.append(
        "Prior versions of this artifact are archived below, oldest first. "
        "Each entry shows the timestamp and the path to the archived JSON "
        "and optional Markdown handoff."
    )
    lines.append("")

    for json_path in json_files:
        m = _TIMESTAMP_PATTERN.match(json_path.name)
        ts = m.group(1) if m else json_path.stem
        lines.append(f"## {ts}")
        lines.append("")
        lines.append(f"- JSON: `{json_path.name}`")
        md_name = json_path.name.replace(".json", ".md")
        md_path = hist_dir / md_name
        if backend.exists(md_path):
            lines.append(f"- Markdown: `{md_name}`")
        lines.append("")

    index_path = hist_dir / "index.md"
    backend.write_text(index_path, "\n".join(lines))


def clear_artifact_history(
    artifact_dir: Path,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Remove all archived history files for an artifact type.

    Deletes all timestamped archive files and the index. The history directory
    itself is left in place to avoid filesystem churn on repeated planning cycles.
    """
    hist_dir = history_dir_for_artifact(artifact_dir, artifact_type)
    if not backend.exists(hist_dir):
        return

    for path in list(backend.glob(hist_dir, "*.json")):
        backend.unlink(path, missing_ok=True)
    for path in list(backend.glob(hist_dir, "*.md")):
        backend.unlink(path, missing_ok=True)


__all__ = [
    "archive_artifact_before_overwrite",
    "clear_artifact_history",
    "history_dir_for_artifact",
    "history_index_path",
    "rebuild_history_index",
]
