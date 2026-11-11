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

    def schedule(self, path: str, *, recursive: bool) -> None:
        del recursive
        if path != self.workspace_root:
            raise ValueError(f"observer outside canonical workspace: {path!r}")
        self.observers += 1

    def unschedule(self, path: str) -> None:
        if path != self.workspace_root:
            raise ValueError(f"observer outside canonical workspace: {path!r}")
        self.observers -= 1
        if self.observers < 0:
            raise AssertionError("observer count went negative")

    def seed_file(self, path: str, content: str) -> None:
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

    def mark_dirty(self, path: str, *, source_tool: str) -> None:
        self.events += 1
        self._pending_dirty[path] = source_tool

    def take_pending_batch(self) -> tuple[str, ...]:
        batch = tuple(sorted(self._pending_dirty))
        self._pending_dirty.clear()
        self._flushed_batches.append(batch)
        return batch

    def coalescing_flush(self) -> tuple[str, ...]:
        batch = self.take_pending_batch()
        for path in batch:
            self.parse(path)
        return batch

    @property
    def flushed_batches(self) -> tuple[tuple[str, ...], ...]:
        return tuple(self._flushed_batches)

    def search(self, pattern: str) -> list[str]:
        return sorted(path for path in self._files if fnmatch.fnmatch(path, pattern))

    def tree(self, base: str, *, max_depth: int = 2) -> dict[str, object]:
        prefix = base.rstrip("/")
        children = sorted(
            path[len(prefix) + 1 :]
            for path in self._files
            if path.startswith(prefix + "/") and path[len(prefix) + 1 :].count("/") < max_depth
        )
        return {"path": base, "children": children}

    def snapshot(self) -> dict[str, object]:
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
