
from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.mcp.artifacts.file_backend import FileBackend


def write_text_if_changed(
    backend: FileBackend,
    path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
    prepare_write: Callable[[], None] | None = None,
) -> bool:
    try:
        existing = backend.read_text(path, encoding=encoding)
    except (KeyError, OSError, UnicodeDecodeError):
        existing = None
    if existing is not None and existing == content:
        return False
    if prepare_write is not None:
        prepare_write()
    backend.write_text(path, content, encoding=encoding)
    return True


def write_bytes_if_changed(
    backend: FileBackend,
    path: Path,
    content: bytes,
    *,
    prepare_write: Callable[[], None] | None = None,
) -> bool:
    try:
        existing = backend.read_bytes(path)
    except (KeyError, OSError):
        existing = None
    if existing is not None and existing == content:
        return False
    if prepare_write is not None:
        prepare_write()
    backend.write_bytes(path, content)
    return True


def _validate_staging_directory(destination: Path, tmp_path: Path) -> None:
    if tmp_path.parent != destination.parent:
        msg = "atomic staging path must be in the same directory as its destination"
        raise ValueError(msg)


def _unique_staging_path(tmp_path: Path) -> Path:
    return tmp_path.with_name(f"{tmp_path.name}.{uuid4().hex}")


def atomic_write_text_if_changed(
    backend: FileBackend,
    destination: Path,
    content: str,
    *,
    tmp_path: Path,
    encoding: str = "utf-8",
    sync_directory: bool = False,
    prepare_write: Callable[[], None] | None = None,
) -> bool:
    _validate_staging_directory(destination, tmp_path)
    try:
        existing = backend.read_text(destination, encoding=encoding)
    except (KeyError, OSError, UnicodeDecodeError):
        existing = None
    if existing is not None and existing == content:
        return False
    if prepare_write is not None:
        prepare_write()
    staging_path = _unique_staging_path(tmp_path)
    try:
        backend.write_text(staging_path, content, encoding=encoding)
        backend.replace(staging_path, destination)
    except BaseException:
        with suppress(OSError):
            backend.unlink(staging_path, missing_ok=True)
        raise
    if sync_directory:
        backend.sync_directory(destination.parent)
    return True


def atomic_write_bytes_if_changed(
    backend: FileBackend,
    destination: Path,
    content: bytes,
    *,
    tmp_path: Path,
    sync_directory: bool = False,
    prepare_write: Callable[[], None] | None = None,
) -> bool:
    _validate_staging_directory(destination, tmp_path)
    try:
        existing = backend.read_bytes(destination)
    except (KeyError, OSError):
        existing = None
    if existing is not None and existing == content:
        return False
    if prepare_write is not None:
        prepare_write()
    staging_path = _unique_staging_path(tmp_path)
    try:
        backend.write_bytes(staging_path, content)
        backend.replace(staging_path, destination)
    except BaseException:
        with suppress(OSError):
            backend.unlink(staging_path, missing_ok=True)
        raise
    if sync_directory:
        backend.sync_directory(destination.parent)
    return True


__all__ = [
    "atomic_write_bytes_if_changed",
    "atomic_write_text_if_changed",
    "write_bytes_if_changed",
    "write_text_if_changed",
]
