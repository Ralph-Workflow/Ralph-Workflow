"""Tests for the change-aware READY cache."""

from __future__ import annotations

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import cache, markers
from ralph.project_policy.models import ReadinessStatus
from ralph.workspace.memory import MemoryWorkspace


def _stack() -> ProjectStack:
    return ProjectStack(primary_language="Python")


def test_cache_miss_when_no_file() -> None:
    ws = MemoryWorkspace()
    assert cache.read_cached_ready(ws, _stack()) is False


def test_cache_returns_ready_only_for_matching_signature() -> None:
    ws = MemoryWorkspace()
    cache.write_cache(ws, _stack(), ReadinessStatus.READY)
    assert cache.read_cached_ready(ws, _stack()) is True


def test_cache_invalidates_on_edit() -> None:
    ws = MemoryWorkspace()
    cache.write_cache(ws, _stack(), ReadinessStatus.READY)
    # Edit a policy file -> signature changes.
    ws.write(f"{markers.CANONICAL_DIR}testing-policy.md", "# edit")
    assert cache.read_cached_ready(ws, _stack()) is False


def test_cache_invalidates_on_deletion() -> None:
    ws = MemoryWorkspace()
    ws.write(f"{markers.CANONICAL_DIR}testing-policy.md", "# content")
    cache.write_cache(ws, _stack(), ReadinessStatus.READY)
    ws.remove(f"{markers.CANONICAL_DIR}testing-policy.md")
    assert cache.read_cached_ready(ws, _stack()) is False


def test_write_cache_skips_non_ready() -> None:
    ws = MemoryWorkspace()
    cache.write_cache(ws, _stack(), ReadinessStatus.REMEDIATION_REQUIRED)
    # Cache file should not exist.
    assert not ws.exists(markers.CACHE_REL_PATH)
    cache.write_cache(ws, _stack(), ReadinessStatus.BLOCKED)
    assert not ws.exists(markers.CACHE_REL_PATH)
    cache.write_cache(ws, _stack(), ReadinessStatus.SKIPPED)
    assert not ws.exists(markers.CACHE_REL_PATH)


def test_cache_invalidates_on_stack_change() -> None:
    ws = MemoryWorkspace()
    cache.write_cache(ws, _stack(), ReadinessStatus.READY)
    # Different stack -> different serialized signature -> cache miss.
    other_stack = ProjectStack(primary_language="Python", secondary_languages=["TypeScript"])
    assert cache.read_cached_ready(ws, other_stack) is False


def test_cache_invalidates_on_directory_signal_create() -> None:
    """AC-04: creating a directory signal path (``benches/``) after a
    cached READY must invalidate the cache so the preflight re-validates
    and re-detects the now-required performance policy.
    """
    ws = MemoryWorkspace()
    cache.write_cache(ws, _stack(), ReadinessStatus.READY)
    ws.create_dir("benches")
    assert cache.read_cached_ready(ws, _stack()) is False


def test_cache_invalidates_on_directory_signal_delete() -> None:
    """AC-04: deleting a directory signal path after a cached READY must
    invalidate the cache. The presence of the directory changed; the
    readiness state may have too.
    """
    ws = MemoryWorkspace()
    ws.create_dir("benches")
    cache.write_cache(ws, _stack(), ReadinessStatus.READY)
    ws.delete("benches", recursive=True)
    assert cache.read_cached_ready(ws, _stack()) is False


def test_cache_invalidates_on_directory_signal_contents_change() -> None:
    """AC-04: the cache signature must change when the sorted listing of
    children inside a directory signal changes (a file is added).
    """
    ws = MemoryWorkspace()
    ws.create_dir("benches")
    cache.write_cache(ws, _stack(), ReadinessStatus.READY)
    ws.write("benches/new_bench.txt", "data")
    assert cache.read_cached_ready(ws, _stack()) is False
