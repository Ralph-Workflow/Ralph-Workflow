"""In-memory deterministic resource probe for the S-1 product baseline harness.

The probe is a black-box seam: it stands in for the workspace,
session, dirty-mark handle, and explore-store surfaces that the
product baseline scenarios exercise, and records every observable
operation (observer registrations, scans, hashes, parses, reads,
writes, events, per-category retained bytes/counts) so the oracle
assertions stay deterministic with no host filesystem or watchdog
dependency.

Deliberately in-memory: the deterministic AC-01..AC-12 state/resource
assertions must not depend on real I/O timing (audit_test_policy);
the responsiveness dimension is measured separately through the
production ``SystemClock`` path in
:mod:`ralph.mcp.explore.bench` (``--product-baseline``).
"""

from __future__ import annotations

import fnmatch
from typing import Final

_CATEGORIES: Final[tuple[str, ...]] = (
    "project_content",
    "workflow_records",
    "workspace_intelligence",
    "operational_records",
    "temporary_data",
)


class ResourceProbe:
    """Deterministic in-memory workspace/session/store probe.

    Surfaces (all duck-typed against the production contracts the
    scenarios consume):

    * ``schedule``/``unschedule`` — observer registration boundary
      (mirrors ``WorkspaceMonitor``'s observer seam; one recursive
      observer per canonical workspace).
    * ``scan``/``hash_paths``/``parse``/``read`` — discovery and
      indexing counters.
    * ``write`` — retained-content write with per-category accounting.
    * ``mark_dirty``/``take_pending_batch``/``coalescing_flush`` —
      durable dirty-queue burst coalescing (AC-06).
    * ``search``/``tree`` — representative in-memory search flows.
    """

    def __init__(self, workspace_root: str = "ws") -> None:
        self.workspace_root = workspace_root
        self.observers = 0
        self.scans = 0
        self.hashes = 0
        self.parses = 0
        self.reads = 0
        self.writes = 0
        self.events = 0
        self._files: dict[str, str] = {}
        self._category_bytes: dict[str, int] = dict.fromkeys(_CATEGORIES, 0)
        self._category_counts: dict[str, int] = dict.fromkeys(_CATEGORIES, 0)
        self._pending_dirty: dict[str, str] = {}
        self._flushed_batches: list[tuple[str, ...]] = []
        self._parsed_paths: list[str] = []

    # --- observation -------------------------------------------------

    def schedule(self, path: str, *, recursive: bool) -> None:
        del recursive  # the oracle asserts on count, not fan-out shape
        if path != self.workspace_root:
            raise ValueError(f"observer outside canonical workspace: {path!r}")
        self.observers += 1

    def unschedule(self, path: str) -> None:
        if path != self.workspace_root:
            raise ValueError(f"observer outside canonical workspace: {path!r}")
        self.observers -= 1
        if self.observers < 0:
            raise AssertionError("observer count went negative")

    # --- discovery / indexing counters --------------------------------

    def seed_file(self, path: str, content: str) -> None:
        """Seed project content without charging the write counter."""
        self._files[path] = content

    def scan(self) -> list[str]:
        self.scans += 1
        return sorted(self._files)

    def hash_paths(self, paths: list[str]) -> None:
        self.hashes += len(paths)

    def parse(self, path: str) -> None:
        self.parses += 1
        self._parsed_paths.append(path)

    def read(self, path: str) -> str:
        self.reads += 1
        return self._files[path]

    @property
    def parsed_paths(self) -> tuple[str, ...]:
        return tuple(self._parsed_paths)

    # --- writes --------------------------------------------------------

    def write(self, path: str, content: str, *, category: str) -> None:
        if category not in _CATEGORIES:
            raise ValueError(f"unknown storage category: {category!r}")
        self.writes += 1
        self.events += 1
        previous = self._files.get(path)
        if previous is not None:
            self._category_bytes[category] -= len(previous.encode("utf-8"))
        else:
            self._category_counts[category] += 1
        self._files[path] = content
        self._category_bytes[category] += len(content.encode("utf-8"))

    # --- dirty-queue burst coalescing ----------------------------------

    def mark_dirty(self, path: str, *, source_tool: str) -> None:
        self.events += 1
        self._pending_dirty[path] = source_tool

    def take_pending_batch(self) -> tuple[str, ...]:
        """Drain the coalesced pending set as ONE burst batch (AC-06)."""
        batch = tuple(sorted(self._pending_dirty))
        self._pending_dirty.clear()
        self._flushed_batches.append(batch)
        return batch

    def coalescing_flush(self) -> tuple[str, ...]:
        """One coalescing flush: reindex consumes the final distinct paths."""
        batch = self.take_pending_batch()
        for path in batch:
            self.parse(path)
        return batch

    @property
    def flushed_batches(self) -> tuple[tuple[str, ...], ...]:
        return tuple(self._flushed_batches)

    # --- representative in-memory search flows -------------------------

    def search(self, pattern: str) -> list[str]:
        """Deterministic glob search over project content (no counters)."""
        return sorted(path for path in self._files if fnmatch.fnmatch(path, pattern))

    def tree(self, base: str, *, max_depth: int = 2) -> dict[str, object]:
        """Deterministic shallow directory tree over project content."""
        prefix = base.rstrip("/")
        children = sorted(
            path[len(prefix) + 1 :]
            for path in self._files
            if path.startswith(prefix + "/") and path[len(prefix) + 1 :].count("/") < max_depth
        )
        return {"path": base, "children": children}

    # --- snapshot ------------------------------------------------------

    def snapshot(self) -> dict[str, object]:
        """Return an immutable counter view the S-1 oracle asserts on."""
        return {
            "observers": self.observers,
            "scans": self.scans,
            "hashes": self.hashes,
            "parses": self.parses,
            "reads": self.reads,
            "writes": self.writes,
            "events": self.events,
            "category_bytes": dict(self._category_bytes),
            "category_counts": dict(self._category_counts),
        }


def category_growth(
    current: dict[str, object], baseline: dict[str, object]
) -> dict[str, tuple[int, int]]:
    """Per-category ``(bytes_delta, count_delta)`` between two snapshots."""
    current_bytes = current["category_bytes"]
    current_counts = current["category_counts"]
    baseline_bytes = baseline["category_bytes"]
    baseline_counts = baseline["category_counts"]
    if not isinstance(current_bytes, dict) or not isinstance(current_counts, dict):
        raise TypeError("snapshot category maps must be dicts")
    if not isinstance(baseline_bytes, dict) or not isinstance(baseline_counts, dict):
        raise TypeError("snapshot category maps must be dicts")
    return {
        category: (
            int(current_bytes.get(category, 0)) - int(baseline_bytes.get(category, 0)),
            int(current_counts.get(category, 0)) - int(baseline_counts.get(category, 0)),
        )
        for category in _CATEGORIES
    }


__all__ = ["ResourceProbe", "category_growth"]
