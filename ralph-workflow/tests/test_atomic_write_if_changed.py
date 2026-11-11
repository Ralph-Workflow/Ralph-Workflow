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
    def exists(self, path: Path) -> bool:
        return True

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del path, encoding
        raise OSError("permission denied")


class _UndecodableReadBackend(_ReplacingCountingBackend):
    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del path, encoding
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")


class _FailingReplaceBackend(_ReplacingCountingBackend):
    def replace(self, source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("publication failed")


class _InterruptedReplaceBackend(_ReplacingCountingBackend):
    def replace(self, source: Path, destination: Path) -> None:
        del source, destination
        raise KeyboardInterrupt


class _CleanupFailingReplaceBackend(_FailingReplaceBackend):
    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        del path, missing_ok
        raise OSError("staging cleanup failed")


def test_atomic_write_regression_rejects_cross_directory_staging_without_mutation() -> None:
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
    backend = _ReplacingCountingBackend()
    destination = Path("/virtual-ws/checkpoint.json")
    tmp_path = Path("/virtual-ws/checkpoint.json.tmp")
    backend._files[destination] = "alpha"

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
    backend = _FailingReplaceBackend()
    destination = Path("/virtual-ws/checkpoint.json")
    tmp_path = Path("/virtual-ws/checkpoint.json.tmp")

    with pytest.raises(OSError, match="publication failed"):
        atomic_write_text_if_changed(backend, destination, "fresh", tmp_path=tmp_path)

    assert backend._files == {}


def test_atomic_write_regression_preserves_publication_error_when_staging_cleanup_fails() -> None:
    backend = _CleanupFailingReplaceBackend()
    destination = Path("/virtual-ws/checkpoint.json")
    tmp_path = Path("/virtual-ws/checkpoint.json.tmp")

    with pytest.raises(OSError, match="publication failed"):
        atomic_write_text_if_changed(backend, destination, "fresh", tmp_path=tmp_path)


def test_atomic_write_regression_cleans_unique_staging_file_when_interrupted() -> None:
    backend = _InterruptedReplaceBackend()
    destination = Path("/virtual-ws/checkpoint.json")
    tmp_path = Path("/virtual-ws/checkpoint.json.tmp")

    with pytest.raises(KeyboardInterrupt):
        atomic_write_text_if_changed(backend, destination, "fresh", tmp_path=tmp_path)

    assert backend._files == {}


def test_atomic_write_regression_fails_open_when_read_text_raises_oserror() -> None:
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
