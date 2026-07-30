"""S-5 regression coverage for durable artifact file writes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts._path_file_backend import PathFileBackend

if TYPE_CHECKING:
    import pytest


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
