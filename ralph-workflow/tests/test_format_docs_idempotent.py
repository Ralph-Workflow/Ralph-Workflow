"""Black-box tests proving the format-doc materializers skip byte-identical writes.

Exercises :func:`materialize_all_format_docs` end-to-end through a
counting in-memory FileBackend (no real filesystem I/O, no
tmp_path, no patching).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.mcp.artifacts.format_docs import (
    FORMAT_DOC_ARTIFACT_TYPES,
    format_doc_workspace_path,
    format_index_workspace_path,
    load_bundled_format_doc,
    load_bundled_format_index,
    materialize_all_format_docs,
)

if TYPE_CHECKING:
    from collections.abc import Dict


class _CountingBackend(FileBackend):
    """In-memory FileBackend that records write_text invocations."""

    def __init__(self) -> None:
        self._files: Dict[Path, str] = {}
        self.write_text_calls: list[Path] = []

    def exists(self, path: Path) -> bool:
        return path in self._files

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del path, parents, exist_ok

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del encoding
        return self._files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        del encoding
        self.write_text_calls.append(path)
        self._files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        del source, destination

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self._files.pop(path, None)
            return
        del self._files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        del path, pattern
        return []


def test_first_materialize_all_writes_one_file_per_returned_path() -> None:
    """First call writes one file per returned path; stored bytes equal bundled content."""
    backend = _CountingBackend()
    workspace_root = Path("/virtual-ws")

    paths = materialize_all_format_docs(workspace_root, backend=backend)

    # write_text count == number of materialized docs (per-type + index).
    assert len(backend.write_text_calls) == len(paths)
    # Per-type docs: stored bytes match the bundled content.
    first_per_type = format_doc_workspace_path(FORMAT_DOC_ARTIFACT_TYPES[0])
    expected_first = load_bundled_format_doc(FORMAT_DOC_ARTIFACT_TYPES[0])
    assert expected_first is not None
    assert backend._files[workspace_root / first_per_type] == expected_first


def test_second_materialize_all_writes_nothing() -> None:
    """Second call with bundled content already on disk performs zero write_text calls."""
    backend = _CountingBackend()
    workspace_root = Path("/virtual-ws")

    first_paths = materialize_all_format_docs(workspace_root, backend=backend)
    assert first_paths  # sanity

    second_paths = materialize_all_format_docs(workspace_root, backend=backend)

    # All writes happened on the FIRST call (the second call skipped everything).
    assert backend.write_text_calls == [workspace_root / path for path in first_paths]
    assert len(backend.write_text_calls) == len(first_paths)
    assert second_paths == first_paths


def test_differing_on_disk_doc_is_rewritten_to_bundled_content() -> None:
    """Pre-seeded stale bytes get rewritten to bundled content (refresh preserved)."""
    backend = _CountingBackend()
    workspace_root = Path("/virtual-ws")
    target_type = FORMAT_DOC_ARTIFACT_TYPES[0]
    relative_path = format_doc_workspace_path(target_type)
    absolute_target = workspace_root / relative_path
    # Pre-seed stale bytes that differ from the bundled doc.
    backend._files[absolute_target] = "stale content that does NOT match bundled"

    materialize_all_format_docs(workspace_root, backend=backend)

    expected_content = load_bundled_format_doc(target_type)
    assert expected_content is not None
    assert backend._files[absolute_target] == expected_content  # refreshed
    # The pre-seeded doc was rewritten on this call (refresh preserved).
    assert absolute_target in backend.write_text_calls


def test_index_doc_round_trips_through_bundled_loader() -> None:
    """Materialized index bytes equal the bundled index content (refresh invariant)."""
    backend = _CountingBackend()
    workspace_root = Path("/virtual-ws")

    materialize_all_format_docs(workspace_root, backend=backend)

    relative_index = format_index_workspace_path()
    absolute_index = workspace_root / relative_index
    assert backend._files[absolute_index] == load_bundled_format_index()
