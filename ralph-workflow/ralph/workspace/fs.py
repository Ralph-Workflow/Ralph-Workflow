from __future__ import annotations

import errno
import os
import shutil
from collections import deque
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.workspace._snapshot import WorkspaceSnapshot
from ralph.workspace.skip import RECURSIVE_SKIP_DIRECTORY_NAMES

MAX_READ_BYTES: int = 5_000_000
MAX_READ_LINES_BYTES: int = MAX_READ_BYTES

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from io import BufferedReader, TextIOWrapper


def _normalize_absolute_path(base: Path, candidate_path: Path) -> Path:
    base_str = os.fspath(base)
    candidate_str = os.fspath(candidate_path)
    joined = candidate_str if candidate_path.is_absolute() else base_str + os.sep + candidate_str
    return Path(os.path.normpath(joined))


def _ensure_regular_mode(mode_bits: int, path: Path) -> None:
    if S_ISDIR(mode_bits):
        raise IsADirectoryError(errno.EISDIR, f"Is a directory: {path}")
    if not S_ISREG(mode_bits):
        raise OSError(errno.EINVAL, f"not a regular file: {path}")


def _open_regular_fd(path: Path) -> int:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        _ensure_regular_mode(os.fstat(fd).st_mode, path)
        os.set_blocking(fd, True)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open_regular(path: Path) -> BufferedReader:
    return os.fdopen(_open_regular_fd(path), "rb")


def _open_regular_text(path: Path) -> TextIOWrapper:
    return os.fdopen(_open_regular_fd(path), "r", encoding="utf-8")


def _require_regular_file(path: Path) -> None:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return
    if not S_ISREG(mode) and not S_ISDIR(mode):
        raise OSError(errno.EINVAL, f"not a regular file: {path}")


def _device_of(path: Path) -> int | None:
    try:
        return path.stat(follow_symlinks=False).st_dev
    except OSError:
        return None


class FsWorkspace:
    __slots__ = (
        "__dict__",
        "_allowed_roots",
        "_backend",
        "_root",
        "explore_index",
    )

    def __init__(
        self,
        root: Path | str,
        *,
        allowed_roots: Sequence[Path | str] | None = None,
        backend: FileBackend = DEFAULT_FILE_BACKEND,
    ) -> None:
        self._root = Path(root).expanduser().resolve()
        requested_allowed = allowed_roots or (self._root,)
        self._allowed_roots = tuple(Path(path).expanduser().resolve() for path in requested_allowed)
        self._backend = backend
        self.explore_index: object | None = None

    def _resolve_candidate(self, path: str) -> Path:
        candidate_path = Path(path)
        base = self._root if not candidate_path.is_absolute() else Path("/")
        candidate = _normalize_absolute_path(base, candidate_path)
        for allowed_root in self._allowed_roots:
            try:
                candidate.relative_to(allowed_root)
            except ValueError:
                continue
            else:
                return candidate
        msg = f"Path '{path}' resolves outside workspace root"
        raise ValueError(msg)

    def _abs(self, path: str) -> Path:
        return self._resolve_candidate(path)

    def read(self, path: str) -> str:
        abs_path = self._abs(path)
        _require_regular_file(abs_path)
        return self._backend.read_text(abs_path, encoding="utf-8")

    def snapshot(self, path: str, *, max_bytes: int | None = None) -> WorkspaceSnapshot:
        abs_path = self._abs(path)
        try:
            with _open_regular(abs_path) as fh:
                st = os.fstat(fh.fileno())
                stat = {
                    "type": "file",
                    "size_bytes": st.st_size,
                    "created_unix": st.st_ctime,
                    "modified_unix": st.st_mtime,
                    "mode": st.st_mode,
                }
                if max_bytes is not None and st.st_size > max_bytes:
                    return WorkspaceSnapshot(stat=stat, content=None)
                raw = fh.read()
        except FileNotFoundError:
            return WorkspaceSnapshot(stat={"type": "missing"}, content=None)
        except IsADirectoryError:
            try:
                st = abs_path.stat()
            except FileNotFoundError:
                return WorkspaceSnapshot(stat={"type": "missing"}, content=None)
            return WorkspaceSnapshot(
                stat={
                    "type": "dir",
                    "size_bytes": 0,
                    "created_unix": st.st_ctime,
                    "modified_unix": st.st_mtime,
                    "mode": st.st_mode,
                },
                content=None,
            )
        return WorkspaceSnapshot(stat=stat, content=raw.decode("utf-8"))

    def write(self, path: str, content: str) -> None:
        p = self._abs(path)
        write_text_if_changed(
            self._backend,
            p,
            content,
            encoding="utf-8",
            prepare_write=lambda: self._backend.mkdir(p.parent, parents=True, exist_ok=True),
        )

    def append(self, path: str, content: str) -> None:
        p = self._abs(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(content)

    def exists(self, path: str) -> bool:
        return self._abs(path).exists()

    def remove(self, path: str) -> None:
        self._backend.unlink(self._abs(path), missing_ok=True)

    def list_dir(self, path: str) -> list[str]:
        p = self._abs(path)
        if not p.is_dir():
            return []
        return [str(item.relative_to(p)) for item in p.iterdir()]

    def is_dir(self, path: str) -> bool:
        return self._abs(path).is_dir()

    def is_file(self, path: str) -> bool:
        return self._abs(path).is_file()

    @property
    def root(self) -> Path:
        return self._root

    def absolute_path(self, path: str) -> str:
        return str(self._abs(path).resolve())

    def read_lines(
        self,
        path: str,
        *,
        start: int | None = None,
        end: int | None = None,
        head: int | None = None,
        tail: int | None = None,
        max_bytes: int | None = None,
    ) -> tuple[str, dict[str, object]]:
        has_range = (start is not None) or (end is not None)
        has_head = head is not None
        has_tail = tail is not None
        mode_count = sum(1 for m in (has_range, has_head, has_tail) if m)
        if mode_count > 1:
            raise ValueError("Only one of (start/end range), head, or tail may be specified")

        abs_path = self._abs(path)
        ceiling = max_bytes if max_bytes is not None else MAX_READ_LINES_BYTES
        returned_lines, total_lines = self._read_lines_once(
            abs_path,
            start=start,
            end=end,
            head=head,
            tail=tail,
            full_read_ceiling=ceiling if mode_count == 0 else None,
            display_path=path,
        )
        truncated = (
            (head is not None and total_lines > head)
            or (tail is not None and total_lines > tail)
            or ((start is not None or end is not None) and end is not None and end < total_lines)
        )

        return "".join(returned_lines), {
            "total_lines": total_lines,
            "returned_lines": len(returned_lines),
            "truncated": truncated,
        }

    @staticmethod
    def _count_lines(abs_path: Path) -> int:
        total = 0
        last_byte: int | None = None
        with _open_regular(abs_path) as fh:
            while True:
                chunk = fh.read(65_536)
                if not chunk:
                    break
                total += chunk.count(b"\n")
                last_byte = chunk[-1]
        if last_byte is not None and last_byte != ord("\n"):
            total += 1
        return total

    @staticmethod
    def _read_lines_once(
        abs_path: Path,
        *,
        start: int | None,
        end: int | None,
        head: int | None,
        tail: int | None,
        full_read_ceiling: int | None,
        display_path: str,
    ) -> tuple[list[str], int]:
        if head is not None:
            selected: list[str] | deque[str] = []
        elif tail is not None:
            selected = deque(maxlen=tail)
        elif start is not None or end is not None:
            selected = []
        else:
            selected = []

        total_lines = 0
        with _open_regular_text(abs_path) as fh:
            file_size = os.fstat(fh.fileno()).st_size
            if full_read_ceiling is not None and file_size > full_read_ceiling:
                raise ValueError(
                    f"File too large for read_lines: {file_size} bytes exceeds "
                    f"limit of {full_read_ceiling} bytes (path={display_path!r}). "
                    "Use a partial read (head/tail/range) or raise max_bytes."
                )
            for line in fh:
                line_index = total_lines
                total_lines += 1
                if head is not None:
                    if line_index < head:
                        selected.append(line)
                elif tail is not None:
                    selected.append(line)
                elif start is not None or end is not None:
                    start_index = max(0, (start - 1) if start is not None else 0)
                    if line_index >= start_index and (end is None or line_index < end):
                        selected.append(line)
                else:
                    selected.append(line)
        return list(selected), total_lines

    def read_bytes(
        self,
        path: str,
        *,
        offset: int = 0,
        limit: int | None = None,
        max_bytes: int | None = None,
    ) -> tuple[str, dict[str, object]]:
        if offset < 0:
            raise ValueError("offset must not be negative")
        if limit is not None and limit < 0:
            raise ValueError("limit must not be negative")

        abs_path = self._abs(path)
        try:
            total_bytes = abs_path.stat().st_size
        except FileNotFoundError:
            raise FileNotFoundError(f"File not found: {path}") from None
        ceiling = max_bytes if max_bytes is not None else MAX_READ_BYTES
        available_bytes = max(0, total_bytes - offset)
        requested_bytes = available_bytes if limit is None else min(available_bytes, limit)
        if requested_bytes > ceiling:
            raise ValueError(
                f"File too large for read_bytes: requested {requested_bytes} bytes exceeds "
                f"limit of {ceiling} bytes (path={path!r}). "
                "Use offset/limit to request a bounded window or raise max_bytes."
            )
        with _open_regular(abs_path) as fh:
            if offset:
                fh.seek(offset)
            raw = fh.read(limit) if limit is not None else fh.read()
        returned_bytes = len(raw)
        truncated = (offset + returned_bytes) < total_bytes
        text = raw.decode("utf-8")
        return text, {
            "total_bytes": total_bytes,
            "returned_bytes": returned_bytes,
            "truncated": truncated,
        }

    def stat(self, path: str) -> dict[str, object]:
        p = self._abs(path)
        try:
            st = p.stat()
        except FileNotFoundError:
            return {"type": "missing"}
        if S_ISDIR(st.st_mode):
            return {
                "type": "dir",
                "size_bytes": 0,
                "created_unix": st.st_ctime,
                "modified_unix": st.st_mtime,
                "mode": st.st_mode,
            }
        return {
            "type": "file" if S_ISREG(st.st_mode) else "special",
            "size_bytes": st.st_size,
            "created_unix": st.st_ctime,
            "modified_unix": st.st_mtime,
            "mode": st.st_mode,
        }

    def mkdirs(self, path: str) -> None:
        self._backend.mkdir(self._abs(path), parents=True, exist_ok=True)

    def move(self, src: str, dest: str, *, overwrite: bool = False) -> None:
        src_abs = self._abs(src)
        dest_abs = self._abs(dest)
        if dest_abs.exists() and not overwrite:
            raise FileExistsError(f"Destination '{dest}' already exists")
        shutil.move(str(src_abs), str(dest_abs))

    def copy(self, src: str, dest: str, *, overwrite: bool = False) -> None:
        src_abs = self._abs(src)
        dest_abs = self._abs(dest)
        if dest_abs.exists() and not overwrite:
            raise FileExistsError(f"Destination '{dest}' already exists")
        if src_abs.is_dir():
            shutil.copytree(str(src_abs), str(dest_abs), dirs_exist_ok=overwrite)
        else:
            self._backend.mkdir(dest_abs.parent, parents=True, exist_ok=True)
            shutil.copy2(str(src_abs), str(dest_abs))

    def delete(self, path: str, *, recursive: bool = False) -> None:
        p = self._abs(path)
        if p.is_dir():
            if not recursive:
                raise IsADirectoryError(f"Path '{path}' is a directory, use recursive=True")
            shutil.rmtree(str(p))
        else:
            p.unlink()

    def allowed_roots(self) -> list[str]:
        return [str(p) for p in self._allowed_roots]

    def iter_files(self, base: str) -> tuple[str, ...]:
        return tuple(self.walk_files(base))

    def walk_files(self, base: str) -> Iterator[str]:
        base_abs = self._abs(base)
        if not base_abs.is_dir() or self._is_skipped_traversal_base(base_abs):
            return
        root_dev = _device_of(base_abs)
        if root_dev is None:
            return
        for root, dirs, files in os.walk(str(base_abs)):
            dirs[:] = [
                d
                for d in dirs
                if d not in RECURSIVE_SKIP_DIRECTORY_NAMES
                and _device_of(Path(root) / d) == root_dev
            ]
            rel_root = Path(root).relative_to(self._root)
            for f in files:
                yield str(rel_root / f)

    def _is_skipped_traversal_base(self, base_abs: Path) -> bool:
        try:
            relative_parts = base_abs.relative_to(self._root).parts
        except ValueError:
            return False
        return any(part in RECURSIVE_SKIP_DIRECTORY_NAMES for part in relative_parts)
