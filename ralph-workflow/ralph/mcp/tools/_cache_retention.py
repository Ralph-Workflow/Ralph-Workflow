
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


@dataclass(frozen=True)
class CachePruneResult:

    removed_paths: tuple[Path, ...]
    retained_bytes: int


@dataclass(frozen=True)
class _CacheEntry:
    path: Path
    resolved_path: Path
    size_bytes: int
    mtime_ns: int


def prune_cache_files(
    files: Iterable[Path],
    *,
    max_total_bytes: int,
    keep_paths: Iterable[Path] = (),
) -> CachePruneResult:
    entries = _read_cache_entries(files)
    total_bytes = sum(entry.size_bytes for entry in entries)
    if total_bytes <= max_total_bytes:
        return CachePruneResult(removed_paths=(), retained_bytes=total_bytes)

    keep_resolved = {_resolve_path(path) for path in keep_paths}
    removed: list[Path] = []
    for entry in sorted(entries, key=_cache_entry_sort_key):
        if total_bytes <= max_total_bytes:
            break
        if entry.resolved_path in keep_resolved:
            continue
        try:
            entry.path.unlink()
        except OSError:
            continue
        total_bytes -= entry.size_bytes
        removed.append(entry.path)

    return CachePruneResult(removed_paths=tuple(removed), retained_bytes=total_bytes)


def _cache_entry_sort_key(entry: _CacheEntry) -> tuple[int, str]:
    return (entry.mtime_ns, str(entry.path))


def _read_cache_entries(files: Iterable[Path]) -> list[_CacheEntry]:
    entries: list[_CacheEntry] = []
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        if not path.is_file():
            continue
        entries.append(
            _CacheEntry(
                path=path,
                resolved_path=_resolve_path(path),
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
            )
        )
    return entries


def _resolve_path(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError:
        return path.absolute()


__all__ = ["CachePruneResult", "prune_cache_files"]
