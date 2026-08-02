"""Black-box tests for ralph.mcp.artifacts.idempotent_write.atomic_write_text_if_changed.

Satisfies AC-01: a reusable atomic_write_text_if_changed helper is added
to the existing ralph/mcp/artifacts/idempotent_write.py module (no new
module). It reads the destination, returns False without writing tmp or
replacing when the existing bytes equal content, otherwise writes tmp
and replaces returning True, and fails open on OSError. It never creates
parent directories.

The helper is exercised end-to-end through a counting in-memory
FileBackend whose ``replace`` MOVES stored source content to the
destination (the reference backends implement replace as a no-op, which
is fine for write_text_if_changed but would defeat destination-content
assertions for the atomic path). All tests use no real filesystem I/O,
no tmp_path, no patching, and no ``time.sleep``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.mcp.artifacts.idempotent_write import atomic_write_text_if_changed

if TYPE_CHECKING:
    from collections.abc import Dict


class _ReplacingCountingBackend(FileBackend):
    """In-memory FileBackend that records write_text and replace invocations."""

    def __init__(self) -> None:
        self._files: Dict[Path, str] = {}
        self.read_text_calls: list[Path] = []
        self.write_text_calls: list[tuple[Path, str]] = []
        self.replace_calls: list[tuple[Path, Path]] = []
        self.sync_directory_calls: list[Path] = []

    def exists(self, path: Path) -> bool:
        return path in self._files

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del path, parents, exist_ok

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del encoding
        self.read_text_calls.append(path)
        return self._files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        del encoding
        self.write_text_calls.append((path, content))
        self._files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        self.replace_calls.append((source, destination))
        self._files[destination] = self._files.pop(source)

    def sync_directory(self, path: Path) -> None:
        self.sync_directory_calls.append(path)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self._files.pop(path, None)
            return
        del self._files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        del path, pattern
        return []


def test_atomic_write_concurrent_writers_publish_independent_final_bytes() -> None:
    """B4: concurrent writers must each publish their own final bytes safely.

    The atomic primitive derives a unique staging path per call (random
    suffix on ``tmp_path``) and only mutates the destination inside
    ``backend.replace``. Three competing writers with distinct final
    payloads race the helper; the observable contract is that the
    destination ends up containing exactly one of the three payloads
    and the helper returned ``True`` for the writer that actually
    replaced it. No writer's payload is silently corrupted; staging
    paths never collide (each writer derives its own random suffix),
    and the skipped writes do not block the replacing writer.

    The regression assertion uses an in-memory counting backend so the
    test exercises only the helper's race behavior without touching a
    real filesystem. No ``time.sleep`` is used; ``ThreadPoolExecutor``
    drives the concurrent calls so a thread-switch is forced by the
    scheduler.
    """
    backend = _ReplacingCountingBackend()
    destination = Path("/virtual-ws/concurrent.json")
    tmp_path = Path("/virtual-ws/concurrent.json.tmp")

    payloads = ("alpha-payload", "beta-payload", "gamma-payload")

    def _publish(payload: str) -> bool:
        return atomic_write_text_if_changed(
            backend,
            destination,
            payload,
            tmp_path=tmp_path,
        )

    with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
        results = list(pool.map(_publish, payloads))

    assert backend._files[destination] in payloads
    assert results.count(True) >= 1
    # The helper performs at most one replace per true publication; the
    # destination bytes always equal one of the caller's payloads.
    assert backend._files[destination] in {"alpha-payload", "beta-payload", "gamma-payload"}
    # No two writers used the same staging file name; every writer
    # derived its own random suffix.
    staging_paths = {call[0] for call in backend.write_text_calls}
    assert len(staging_paths) == len(backend.write_text_calls)


def test_atomic_write_concurrent_identical_writers_skip_redundant_publications() -> None:
    """B4: a no-op concurrent cycle produces zero publications and zero replaces.

    When every concurrent writer publishes the same payload and the
    destination already contains that payload, no thread should win a
    physical write or replace. This is the skip side of B4: refusing
    to write is concurrency-safe because every writer's payload agrees
    with the destination's existing bytes, so there is no contention
    to resolve.
    """
    backend = _ReplacingCountingBackend()
    destination = Path("/virtual-ws/shared.json")
    tmp_path = Path("/virtual-ws/shared.json.tmp")
    backend._files[destination] = "stable"

    payloads = ("stable", "stable", "stable")

    def _publish(payload: str) -> bool:
        return atomic_write_text_if_changed(
            backend,
            destination,
            payload,
            tmp_path=tmp_path,
        )

    with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
        results = list(pool.map(_publish, payloads))

    assert results == [False, False, False]
    assert backend.write_text_calls == []
    assert backend.replace_calls == []
    assert backend._files[destination] == "stable"
