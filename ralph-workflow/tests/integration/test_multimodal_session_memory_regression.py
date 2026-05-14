"""Regression harness for bounded multimodal live-session retention."""

from __future__ import annotations

import json
import tracemalloc
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from ralph.mcp.multimodal.capabilities import UNKNOWN_IDENTITY, MultimodalModelIdentity
from ralph.mcp.multimodal.resources import MediaManifest
from ralph.mcp.tools.workspace import MEDIA_READ_CAPABILITY, handle_read_media
from ralph.prompts.materialize import collect_media_entries_for_phase
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path


_ARTIFACT_SIZE_BYTES = 8 * 1024
_WARMUP_CALLS = 1
_SAMPLE_CALLS = 6
_CURRENT_BUDGET_BYTES = 256_000
_INDEX_SIZE_BUDGET_BYTES = 256
_PEAK_BUDGET_BYTES = 3 * 1024 * 1024


@dataclass
class _SessionWithDrain:
    allowed_capability: str | None = None
    drain: str = "development"
    session_id: str = "test-session"
    media_manifest: MediaManifest = field(default_factory=MediaManifest)
    model_identity: MultimodalModelIdentity = field(default=UNKNOWN_IDENTITY)

    def check_capability(self, capability: str) -> object:
        return capability == self.allowed_capability

    def check_edit_area(self, _: str) -> object:
        return True


def _read_media(session: _SessionWithDrain, workspace: FsWorkspace) -> Any:
    return handle_read_media(session, workspace, {"path": "report.pdf"})


@pytest.mark.integration
@pytest.mark.timeout_seconds(10)
def test_multimodal_session_memory_regression(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)
    session = _SessionWithDrain(MEDIA_READ_CAPABILITY)
    media_file = tmp_path / "report.pdf"
    media_file.write_bytes(b"%PDF-1.4\n" + b"x" * (_ARTIFACT_SIZE_BYTES - 9))
    index_path = tmp_path / ".agent" / "tmp" / "development_media_session.json"

    for _ in range(_WARMUP_CALLS):
        result = _read_media(session, workspace)
        assert result.is_error is False

    current_samples: list[int] = []
    index_sizes: list[int] = []
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        for _ in range(_SAMPLE_CALLS):
            result = _read_media(session, workspace)
            assert result.is_error is False
            current, _peak = tracemalloc.get_traced_memory()
            current_samples.append(current)
            index_sizes.append(index_path.stat().st_size)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    persisted = json.loads(index_path.read_text(encoding="utf-8"))
    assert len(persisted["artifacts"]) == 1
    assert len(collect_media_entries_for_phase(workspace, "development")) == 1
    assert len(session.media_manifest.list_entries()) == 1
    assert max(current_samples) - min(current_samples) < _CURRENT_BUDGET_BYTES
    assert max(index_sizes) - min(index_sizes) < _INDEX_SIZE_BUDGET_BYTES
    assert peak < _PEAK_BUDGET_BYTES
