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
    """In-memory FileBackend that records write_text and replace invocations.

    ``replace(source, destination)`` MOVES stored source content to the
    destination (``self._files[destination] = self._files.pop(source)``)
    so atomic-write assertions on destination content are reachable.
    """

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


class _RaisingReadBackend(_ReplacingCountingBackend):
    """FileBackend that claims the destination exists but read_text always raises OSError."""

    def exists(self, path: Path) -> bool:
        return True

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del path, encoding
        raise OSError("permission denied")


class _UndecodableReadBackend(_ReplacingCountingBackend):
    """FileBackend that cannot decode an existing text destination."""

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del path, encoding
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")


class _FailingReplaceBackend(_ReplacingCountingBackend):
    """Persistence boundary that fails publication after staging content."""

    def replace(self, source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("publication failed")


class _InterruptedReplaceBackend(_ReplacingCountingBackend):
    """Persistence boundary interrupted after it stages a publication."""

    def replace(self, source: Path, destination: Path) -> None:
        del source, destination
        raise KeyboardInterrupt


class _CleanupFailingReplaceBackend(_FailingReplaceBackend):
    """Publication boundary whose best-effort staging cleanup also fails."""

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        del path, missing_ok
        raise OSError("staging cleanup failed")


def test_atomic_write_regression_rejects_cross_directory_staging_without_mutation() -> None:
    """S-2: atomic publication refuses staging outside the destination directory."""
    backend = _ReplacingCountingBackend()
    destination = Path("/virtual-ws/checkpoint.json")

    with pytest.raises(ValueError, match="same directory"):
        atomic_write_text_if_changed(
            backend,
            destination,
            "fresh",
            tmp_path=Path("/other-ws/checkpoint.json.tmp"),
        )

    assert backend.read_text_calls == []
    assert backend.write_text_calls == []
    assert backend.replace_calls == []
    assert backend._files == {}


def test_atomic_write_regression_writes_and_replaces_when_destination_absent() -> None:
    """AC-01: fresh destination performs one tmp-write plus one replace returning True.

    Verifies the changed-content half of AC-01: when the destination does
    not exist, ``atomic_write_text_if_changed`` writes the content to the
    tmp path, replaces the destination, and returns ``True``. The final
    ``_files[destination]`` equals the requested content and the tmp path
    is absent.
    """
    backend = _ReplacingCountingBackend()
    destination = Path("/virtual-ws/checkpoint.json")
    tmp_path = Path("/virtual-ws/checkpoint.json.tmp")

    result = atomic_write_text_if_changed(
        backend,
        destination,
        "fresh",
        tmp_path=tmp_path,
    )

    assert result is True
    staging_path = backend.write_text_calls[0][0]
    assert backend.write_text_calls == [(staging_path, "fresh")]
    assert backend.replace_calls == [(staging_path, destination)]
    assert staging_path.parent == tmp_path.parent
    assert staging_path.name.startswith(f"{tmp_path.name}.")
    assert backend._files[destination] == "fresh"
    assert staging_path not in backend._files


def test_atomic_write_regression_skips_write_and_replace_when_content_identical() -> None:
    """AC-01: byte-identical destination content returns False with zero writes and zero replaces.

    Verifies the skip half of AC-01: when the destination already contains
    exactly the requested content, ``atomic_write_text_if_changed`` must
    not write the tmp path and must not call ``replace``, returning
    ``False`` instead. The stored destination bytes remain unchanged.
    """
    backend = _ReplacingCountingBackend()
    destination = Path("/virtual-ws/checkpoint.json")
    tmp_path = Path("/virtual-ws/checkpoint.json.tmp")
    backend._files[destination] = "alpha"  # seeding is the documented test seam

    result = atomic_write_text_if_changed(
        backend,
        destination,
        "alpha",
        tmp_path=tmp_path,
    )

    assert result is False
    assert backend.write_text_calls == []
    assert backend.replace_calls == []
    assert backend._files[destination] == "alpha"


def test_atomic_write_skips_deferred_preparation_and_directory_sync_when_identical() -> None:
    """An identical replay causes neither parent mutation nor durability barrier."""
    backend = _ReplacingCountingBackend()
    destination = Path("/virtual-ws/checkpoint.json")
    tmp_path = Path("/virtual-ws/checkpoint.json.tmp")
    backend._files[destination] = "same"
    prepared: list[str] = []

    result = atomic_write_text_if_changed(
        backend,
        destination,
        "same",
        tmp_path=tmp_path,
        sync_directory=True,
        prepare_write=lambda: prepared.append("parent-ready"),
    )

    assert result is False
    assert prepared == []
    assert backend.write_text_calls == []
    assert backend.replace_calls == []
    assert backend.sync_directory_calls == []


def test_atomic_write_regression_writes_and_replaces_when_content_changed() -> None:
    """AC-01: changed destination content triggers one write_text plus one replace returning True.

    Verifies that when the destination exists but its bytes differ from
    the requested content, ``atomic_write_text_if_changed`` still performs
    the full tmp-write plus replace cycle, returning ``True`` and leaving
    the destination populated with the new bytes.
    """
    backend = _ReplacingCountingBackend()
    destination = Path("/virtual-ws/checkpoint.json")
    tmp_path = Path("/virtual-ws/checkpoint.json.tmp")
    backend._files[destination] = "old"

    result = atomic_write_text_if_changed(
        backend,
        destination,
        "new",
        tmp_path=tmp_path,
    )

    assert result is True
    staging_path = backend.write_text_calls[0][0]
    assert backend.write_text_calls == [(staging_path, "new")]
    assert backend.replace_calls == [(staging_path, destination)]
    assert staging_path.parent == tmp_path.parent
    assert staging_path.name.startswith(f"{tmp_path.name}.")
    assert backend._files[destination] == "new"
    assert staging_path not in backend._files


def test_atomic_write_regression_removes_unique_staging_file_when_publication_fails() -> None:
    """S-3: failed publication cleans its unique transient staging file."""
    backend = _FailingReplaceBackend()
    destination = Path("/virtual-ws/checkpoint.json")
    tmp_path = Path("/virtual-ws/checkpoint.json.tmp")

    with pytest.raises(OSError, match="publication failed"):
        atomic_write_text_if_changed(backend, destination, "fresh", tmp_path=tmp_path)

    assert backend._files == {}


def test_atomic_write_regression_preserves_publication_error_when_staging_cleanup_fails() -> None:
    """S-3: a failed best-effort cleanup cannot mask the publication failure."""
    backend = _CleanupFailingReplaceBackend()
    destination = Path("/virtual-ws/checkpoint.json")
    tmp_path = Path("/virtual-ws/checkpoint.json.tmp")

    with pytest.raises(OSError, match="publication failed"):
        atomic_write_text_if_changed(backend, destination, "fresh", tmp_path=tmp_path)


def test_atomic_write_regression_cleans_unique_staging_file_when_interrupted() -> None:
    """S-3: interruption after staging leaves no transient file in the watched tree."""
    backend = _InterruptedReplaceBackend()
    destination = Path("/virtual-ws/checkpoint.json")
    tmp_path = Path("/virtual-ws/checkpoint.json.tmp")

    with pytest.raises(KeyboardInterrupt):
        atomic_write_text_if_changed(backend, destination, "fresh", tmp_path=tmp_path)

    assert backend._files == {}


def test_atomic_write_regression_fails_open_when_read_text_raises_oserror() -> None:
    """AC-01: fail-open path -- OSError on the read falls through to a real write plus replace.

    Verifies the fail-open half of AC-01: when ``exists()`` reports True
    but ``read_text`` raises ``OSError``, ``atomic_write_text_if_changed``
    must not silently skip; it must perform the tmp-write plus replace
    cycle and return ``True``.
    """
    backend = _RaisingReadBackend()
    destination = Path("/virtual-ws/locked.json")
    tmp_path = Path("/virtual-ws/locked.json.tmp")

    result = atomic_write_text_if_changed(
        backend,
        destination,
        "recovered",
        tmp_path=tmp_path,
    )

    assert result is True
    staging_path = backend.write_text_calls[0][0]
    assert backend.write_text_calls == [(staging_path, "recovered")]
    assert backend.replace_calls == [(staging_path, destination)]
    assert staging_path.parent == tmp_path.parent
    assert staging_path.name.startswith(f"{tmp_path.name}.")
    assert backend._files[destination] == "recovered"


def test_atomic_text_write_regression_recovers_from_undecodable_destination() -> None:
    """S-3: undecodable destination content must not block atomic self-healing."""
    backend = _UndecodableReadBackend()
    destination = Path("/virtual-ws/invalid.json")
    tmp_path = Path("/virtual-ws/invalid.json.tmp")

    result = atomic_write_text_if_changed(
        backend,
        destination,
        "recovered",
        tmp_path=tmp_path,
    )

    assert result is True
    staging_path = backend.write_text_calls[0][0]
    assert backend.write_text_calls == [(staging_path, "recovered")]
    assert backend.replace_calls == [(staging_path, destination)]
    assert staging_path.parent == tmp_path.parent
    assert staging_path.name.startswith(f"{tmp_path.name}.")


def test_atomic_write_concurrent_writers_publish_independent_final_bytes() -> None:
    """B4: concurrent writers must each publish their own final bytes safely."""

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
    assert backend._files[destination] in {"alpha-payload", "beta-payload", "gamma-payload"}
    staging_paths = {call[0] for call in backend.write_text_calls}
    assert len(staging_paths) == len(backend.write_text_calls)


def test_atomic_write_concurrent_identical_writers_skip_redundant_publications() -> None:
    """B4: a no-op concurrent cycle produces zero publications and zero replaces."""

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
