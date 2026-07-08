"""Regression harness for upstream embedded-media in-memory retention.

wt-024 memory-perf AC-04: when a workspace is threaded through
``normalize_upstream_content_blocks``, the embedded-data blocks must
NOT retain their raw payload in the MediaManifest for the entry
lifetime. The fix wires a durable cache file + lazy ``byte_loader``
so the manifest's ``retain_raw_bytes`` evaluates False.

This test mirrors the pattern at
``test_multimodal_session_memory_regression.py`` exactly (FsWorkspace
for tracemalloc fidelity, 5 iterations of 256 KiB payloads, 2 MiB
retained-delta cap).
"""

from __future__ import annotations

import base64
import gc
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

_ITERATION_COUNT = 5
_ARTIFACT_SIZE_BYTES = 256 * 1024
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
    """With workspace threaded, the manifest entry must wire byte_loader +
    cache_path so retain_raw_bytes is False (AC-04)."""
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
    if entry is None:  # pragma: no cover — URI parsing fallback
        pytest.skip("could not resolve artifact_id from URI")
    assert entry.cache_path, "expected cache_path to be wired when workspace is threaded"
    assert entry._byte_loader is not None
    assert entry._raw_bytes is None, (
        "retain_raw_bytes must be False when byte_loader+cache_path are wired"
    )


@pytest.mark.integration
@pytest.mark.timeout_seconds(10)
def test_upstream_no_workspace_keeps_raw_bytes() -> None:
    """Legacy contract: when NO workspace is threaded, raw_bytes MUST be
    retained in memory (preserves backward compat with callers that pass
    session=None or rely on in-memory entry)."""
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
    if entry is None:  # pragma: no cover
        pytest.skip("could not resolve artifact_id from URI")
    assert entry._raw_bytes == payload, "without a workspace, legacy contract must retain raw_bytes"
    assert entry.cache_path == ""
    assert entry._byte_loader is None


@pytest.mark.integration
@pytest.mark.timeout_seconds(10)
def test_upstream_media_memory_regression(tmp_path: Path) -> None:
    """Drives N embedded blocks ~256 KiB each through the workspace thread and
    asserts the retained-memory delta stays under 2 MiB (AC-04).

    Without the workspace thread, raw_bytes would be pinned in the manifest
    for the entry lifetime, pushing retained delta >> 2 MiB after even a few
    iterations. With the workspace thread the manifest entries keep only a
    cache_path + lazy byte_loader; the original payload is reachable only
    through the cache file (off-heap relative to the manifest).
    """
    workspace = FsWorkspace(tmp_path)
    payload = b"%PNG-FAKE\n" + b"x" * (_ARTIFACT_SIZE_BYTES - 10)

    retained_deltas: list[int] = []

    gc.collect()
    tracemalloc.start()
    baseline_current, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()

    for _ in range(_ITERATION_COUNT):
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

    tracemalloc.stop()

    post_warmup = retained_deltas[1:]
    assert post_warmup
    # Use the max so a single noisy iteration fails loudly rather than the
    # average hiding a regression.
    worst = max(post_warmup)
    assert worst <= _RETAINED_DELTA_LIMIT, (
        f"upstream-media retention regression: worst retained delta "
        f"{worst} bytes > {_RETAINED_DELTA_LIMIT}-byte budget after "
        f"{_ITERATION_COUNT} iterations of {_ARTIFACT_SIZE_BYTES}-byte blocks"
    )
