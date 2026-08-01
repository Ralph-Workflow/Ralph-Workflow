"""Black-box byte-persistence contracts for idempotent artifact writes."""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.artifacts.idempotent_write import (
    atomic_write_bytes_if_changed,
    write_bytes_if_changed,
)


class _ByteBackend:
    """In-memory persistence boundary that exposes byte-level outcomes."""

    def __init__(self) -> None:
        self.files: dict[Path, bytes] = {}
        self.byte_writes: list[tuple[Path, bytes]] = []
        self.replacements: list[tuple[Path, Path]] = []
        self.directory_syncs: list[Path] = []

    def exists(self, path: Path) -> bool:
        return path in self.files

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del path, parents, exist_ok

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        return self.files[path].decode(encoding)

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        self.files[path] = content.encode(encoding)

    def read_bytes(self, path: Path) -> bytes:
        return self.files[path]

    def write_bytes(self, path: Path, content: bytes) -> None:
        self.byte_writes.append((path, content))
        self.files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        self.replacements.append((source, destination))
        self.files[destination] = self.files.pop(source)

    def sync_directory(self, path: Path) -> None:
        self.directory_syncs.append(path)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self.files.pop(path, None)
            return
        del self.files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        del path, pattern
        return []


class _UnreadableByteBackend(_ByteBackend):
    """Byte boundary that cannot read its destination but can publish a replacement."""

    def read_bytes(self, path: Path) -> bytes:
        del path
        raise OSError("permission denied")


def test_byte_write_regression_identical_replay_has_no_mutation() -> None:
    """S-3: identical byte content does not write or prepare a stable path."""
    backend = _ByteBackend()
    path = Path("/workspace/value.bin")
    backend.files[path] = b"\x00same\xff"
    prepared: list[str] = []

    changed = write_bytes_if_changed(
        backend,
        path,
        b"\x00same\xff",
        prepare_write=lambda: prepared.append("prepared"),
    )

    assert changed is False
    assert backend.byte_writes == []
    assert prepared == []
    assert backend.files[path] == b"\x00same\xff"


def test_byte_write_regression_changed_and_unreadable_content_publishes_exact_bytes() -> None:
    """S-3: changed and unreadable content fail open to exact requested bytes."""
    path = Path("/workspace/value.bin")
    changed_backend = _ByteBackend()
    changed_backend.files[path] = b"old"
    unreadable_backend = _UnreadableByteBackend()

    assert write_bytes_if_changed(changed_backend, path, b"new\x00") is True
    assert write_bytes_if_changed(unreadable_backend, path, b"new\x00") is True

    assert changed_backend.byte_writes == [(path, b"new\x00")]
    assert changed_backend.files[path] == b"new\x00"
    assert unreadable_backend.byte_writes == [(path, b"new\x00")]
    assert unreadable_backend.files[path] == b"new\x00"


def test_atomic_byte_write_regression_identical_replay_skips_write_replace_and_barrier() -> None:
    """S-3: an identical atomic replay performs no staging, publish, or directory sync."""
    backend = _ByteBackend()
    destination = Path("/workspace/state.db")
    temporary = Path("/workspace/state.db.tmp")
    backend.files[destination] = b"same"
    prepared: list[str] = []

    changed = atomic_write_bytes_if_changed(
        backend,
        destination,
        b"same",
        tmp_path=temporary,
        sync_directory=True,
        prepare_write=lambda: prepared.append("prepared"),
    )

    assert changed is False
    assert backend.byte_writes == []
    assert backend.replacements == []
    assert backend.directory_syncs == []
    assert prepared == []


def test_atomic_byte_write_regression_fails_open_when_destination_is_unreadable() -> None:
    """S-3: unreadable atomic destinations still publish the requested byte sequence."""
    backend = _UnreadableByteBackend()
    destination = Path("/workspace/state.db")
    temporary = Path("/workspace/state.db.tmp")

    changed = atomic_write_bytes_if_changed(
        backend,
        destination,
        b"recovered",
        tmp_path=temporary,
    )

    assert changed is True
    assert backend.byte_writes == [(temporary, b"recovered")]
    assert backend.replacements == [(temporary, destination)]
    assert backend.files[destination] == b"recovered"


def test_atomic_byte_write_regression_changed_content_publishes_exact_bytes_once() -> None:
    """S-3: changed bytes stage once, publish atomically, and sync only when requested."""
    backend = _ByteBackend()
    destination = Path("/workspace/state.db")
    temporary = Path("/workspace/state.db.tmp")

    changed = atomic_write_bytes_if_changed(
        backend,
        destination,
        b"new\x00",
        tmp_path=temporary,
        sync_directory=True,
    )

    assert changed is True
    assert backend.byte_writes == [(temporary, b"new\x00")]
    assert backend.replacements == [(temporary, destination)]
    assert backend.directory_syncs == [destination.parent]
    assert backend.files[destination] == b"new\x00"
    assert temporary not in backend.files
