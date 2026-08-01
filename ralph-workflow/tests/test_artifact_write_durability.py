"""S-5 regression coverage for durable artifact file writes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts._path_file_backend import PathFileBackend
from ralph.mcp.artifacts.idempotent_write import atomic_write_text_if_changed
from tests.test_artifact_format_docs_memory_backend import MemoryBackend

if TYPE_CHECKING:
    import pytest


class _RecordingBackend(MemoryBackend):
    def __init__(self) -> None:
        super().__init__()
        self.synced_directories: list[Path] = []

    def sync_directory(self, path: Path) -> None:
        self.synced_directories.append(path)


def test_artifact_write_regression_atomic_publish_syncs_destination_directory(
    tmp_path: Path,
) -> None:
    """S-6: every atomic artifact publication makes its rename durable."""
    backend = _RecordingBackend()
    destination = tmp_path / ".agent" / "artifacts" / "plan.md"
    backend.mkdir(destination.parent, parents=True, exist_ok=True)

    atomic_write_text_if_changed(
        backend,
        destination,
        "durable content",
        tmp_path=destination.with_suffix(".md.tmp"),
        sync_directory=True,
    )

    assert backend.read_text(destination) == "durable content"
    assert backend.synced_directories == [destination.parent]


def test_artifact_write_regression_fsyncs_written_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-5: writing an artifact file flushes it to the operating system."""
    fsynced_descriptors: list[int] = []
    monkeypatch.setattr(os, "fsync", fsynced_descriptors.append)

    path = tmp_path / "artifact.md"
    PathFileBackend().write_text(path, "durable content")

    assert path.read_text() == "durable content"
    assert len(fsynced_descriptors) == 1


def test_artifact_write_regression_fsyncs_written_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-3: changed byte content keeps the concrete backend's durability barrier."""
    fsynced_descriptors: list[int] = []
    monkeypatch.setattr(os, "fsync", fsynced_descriptors.append)

    path = tmp_path / "artifact.bin"
    payload = b"\x00durable\xff"
    PathFileBackend().write_bytes(path, payload)

    assert path.read_bytes() == payload
    assert len(fsynced_descriptors) == 1
