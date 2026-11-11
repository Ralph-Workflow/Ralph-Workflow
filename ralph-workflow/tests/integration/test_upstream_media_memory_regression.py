from __future__ import annotations

import base64
import gc
import statistics
import time
import tracemalloc
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from ralph.mcp.multimodal.resources import MediaManifest
from ralph.mcp.upstream.client import normalize_upstream_content_blocks
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from ralph.mcp.multimodal._manifest_entry import ManifestEntry


pytestmark = pytest.mark.subprocess_e2e

_ITERATION_COUNT = 3
_ARTIFACT_SIZE_BYTES = 64 * 1024
_RETAINED_DELTA_LIMIT = 2_000_000


@dataclass
class _SessionWithManifest:
    session_id: str = "test-upstream-session"
    media_manifest: MediaManifest = field(default_factory=MediaManifest)

    def check_capability(self, _capability: str) -> object:
        return False

    def check_edit_area(self, _area: str) -> object:
        return False


def _make_embedded_image_block(payload: bytes) -> dict[str, object]:
    return {
        "type": "image",
        "title": "embedded.png",
        "mimeType": "image/png",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(payload).decode("ascii"),
        },
    }


def _resolve_entry(session: _SessionWithManifest, block: object) -> ManifestEntry | None:
    if not isinstance(block, dict):
        return None
    uri_obj = block.get("uri")
    if not isinstance(uri_obj, str):
        return None
    artifact_id = uri_obj.rsplit("/", maxsplit=1)[-1]
    return session.media_manifest.get(artifact_id)


@pytest.mark.integration
@pytest.mark.timeout_seconds(10)
def test_upstream_workspace_threads_byte_loader(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)
    session = _SessionWithManifest()
    payload = b"x" * 1024
    result: dict[str, object] = {
        "content": [_make_embedded_image_block(payload)],
    }
    normalize_upstream_content_blocks(
        result,
        server_name="test-server",
        tool_name="upstream_test_tool",
        session=session,
        workspace=workspace,
    )
    blocks = result["content"]
    assert isinstance(blocks, list)
    block = blocks[0]
    entry = _resolve_entry(session, block)
    if entry is None:
        pytest.skip("could not resolve artifact_id from URI")
    assert entry.cache_path, "expected cache_path to be wired when workspace is threaded"
    assert entry._byte_loader is not None
    assert entry._raw_bytes is None, (
        "retain_raw_bytes must be False when byte_loader+cache_path are wired"
    )


@pytest.mark.integration
@pytest.mark.timeout_seconds(10)
def test_upstream_no_workspace_keeps_raw_bytes() -> None:
    session = _SessionWithManifest()
    payload = b"x" * 256
    result: dict[str, object] = {
        "content": [_make_embedded_image_block(payload)],
    }
    normalize_upstream_content_blocks(
        result,
        server_name="test-server",
        tool_name="upstream_test_tool",
        session=session,
        workspace=None,
    )
    blocks = result["content"]
    assert isinstance(blocks, list)
    block = blocks[0]
    entry = _resolve_entry(session, block)
    if entry is None:
        pytest.skip("could not resolve artifact_id from URI")
    assert entry._raw_bytes == payload, "without a workspace, legacy contract must retain raw_bytes"
    assert entry.cache_path == ""
    assert entry._byte_loader is None


@pytest.mark.integration
@pytest.mark.timeout_seconds(10)
def test_upstream_media_memory_regression(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)
    payload = b"%PNG-FAKE\n" + b"x" * (_ARTIFACT_SIZE_BYTES - 10)

    retained_deltas: list[int] = []
    elapsed_seconds: list[float] = []

    gc.collect()
    tracemalloc.start()
    baseline_current, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()

    for _ in range(_ITERATION_COUNT):
        started = time.perf_counter()
        session = _SessionWithManifest()
        content_blocks: list[dict[str, object]] = [_make_embedded_image_block(payload)]
        result_content: list[dict[str, object]] = content_blocks
        result: dict[str, object] = {"content": result_content}
        normalize_upstream_content_blocks(
            result,
            server_name="test-server",
            tool_name="upstream_test_tool",
            session=session,
            workspace=workspace,
        )
        current_current, _ = tracemalloc.get_traced_memory()
        retained_deltas.append(current_current - baseline_current)
        elapsed_seconds.append(time.perf_counter() - started)

    tracemalloc.stop()

    post_warmup = retained_deltas[1:]
    post_warmup_elapsed = elapsed_seconds[1:]
    assert post_warmup
    assert post_warmup_elapsed
    worst = max(post_warmup)
    early_retained = post_warmup[:2]
    late_retained = post_warmup[-2:]
    early_elapsed = post_warmup_elapsed[:2]
    late_elapsed = post_warmup_elapsed[-2:]
    print(
        "upstream_media_memory_characterization "
        f"cycles={_ITERATION_COUNT} warmup=1 "
        f"early_retained_mean={statistics.mean(early_retained):.0f} "
        f"late_retained_mean={statistics.mean(late_retained):.0f} "
        f"retained_spread={max(post_warmup) - min(post_warmup)} "
        f"worst_retained={worst} "
        f"early_elapsed_mean_ms={statistics.mean(early_elapsed) * 1000:.3f} "
        f"late_elapsed_mean_ms={statistics.mean(late_elapsed) * 1000:.3f} "
        f"early_elapsed_max_ms={max(early_elapsed) * 1000:.3f} "
        f"late_elapsed_max_ms={max(late_elapsed) * 1000:.3f}"
    )
    assert worst <= _RETAINED_DELTA_LIMIT, (
        f"upstream-media retention regression: worst retained delta "
        f"{worst} bytes > {_RETAINED_DELTA_LIMIT}-byte budget after "
        f"{_ITERATION_COUNT} iterations of {_ARTIFACT_SIZE_BYTES}-byte blocks"
    )
