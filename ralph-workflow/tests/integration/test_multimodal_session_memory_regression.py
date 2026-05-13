"""Regression harness for bounded multimodal live-session retention."""

from __future__ import annotations

import gc
import json
import tracemalloc
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.multimodal.capabilities import UNKNOWN_IDENTITY, MultimodalModelIdentity
from ralph.mcp.multimodal.resources import MediaManifest
from ralph.mcp.tools.workspace import MEDIA_READ_CAPABILITY, handle_read_media
from ralph.prompts.materialize import collect_media_entries_for_phase
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path

_ITERATION_COUNT = 20
_ARTIFACT_SIZE_BYTES = 256 * 1024
_RETAINED_DELTA_SPREAD_LIMIT = 2_000_000
_PEAK_DELTA_LIMIT = 6_000_000
_FINAL_RETAINED_DELTA_LIMIT = 2_000_000
_SESSION_INDEX_SIZE_SPREAD_LIMIT = 2_048


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


@pytest.mark.integration
@pytest.mark.timeout_seconds(10)
def test_multimodal_session_memory_regression(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)
    session = _SessionWithDrain(MEDIA_READ_CAPABILITY)
    media_file = tmp_path / "report.pdf"
    media_file.write_bytes(b"%PDF-1.4\n" + b"x" * (_ARTIFACT_SIZE_BYTES - 9))

    retained_deltas: list[int] = []
    session_index_sizes: list[int] = []

    gc.collect()
    tracemalloc.start()
    baseline_current, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()

    for _ in range(_ITERATION_COUNT):
        result = handle_read_media(session, workspace, {"path": "report.pdf"})
        assert result.is_error is False

        gc.collect()
        current_current, _ = tracemalloc.get_traced_memory()
        retained_deltas.append(current_current - baseline_current)

        index_path = tmp_path / ".agent" / "tmp" / "development_media_session.json"
        session_index_sizes.append(index_path.stat().st_size)

    gc.collect()
    final_current, peak_current = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    post_warmup_deltas = retained_deltas[1:]
    post_warmup_sizes = session_index_sizes[1:]
    assert post_warmup_deltas
    assert post_warmup_sizes

    assert max(post_warmup_deltas) - min(post_warmup_deltas) <= _RETAINED_DELTA_SPREAD_LIMIT
    assert peak_current - baseline_current <= _PEAK_DELTA_LIMIT
    assert final_current - baseline_current <= _FINAL_RETAINED_DELTA_LIMIT
    assert max(post_warmup_sizes) - min(post_warmup_sizes) <= _SESSION_INDEX_SIZE_SPREAD_LIMIT

    index_path = tmp_path / ".agent" / "tmp" / "development_media_session.json"
    persisted = json.loads(index_path.read_text(encoding="utf-8"))
    assert len(persisted["artifacts"]) == 1
    assert len(collect_media_entries_for_phase(workspace, "development")) == 1
    assert len(session.media_manifest.list_entries()) == 1
