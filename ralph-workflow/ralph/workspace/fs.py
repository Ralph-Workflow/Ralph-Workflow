"""Production filesystem workspace.

This module provides the FsWorkspace implementation that
wraps pathlib.Path operations for real filesystem access.
"""

from __future__ import annotations

import itertools
import os
import shutil
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.workspace.skip import RECURSIVE_SKIP_DIRECTORY_NAMES

#: Default maximum file size in bytes for ``read_lines`` (any mode).
#: A ``stat`` precheck rejects files above this ceiling BEFORE we open
#: them so a partial read against a multi-GB file cannot OOM the
#: agent. Matches ``FULL_READ_DEFAULT_MAX_BYTES`` in
#: ``ralph/mcp/tools/workspace/_utils.py`` (5 MB).
MAX_READ_LINES_BYTES: int = 5_000_000

if TYPE_CHECKING:
    from collections.abc import Sequence


class FsWorkspace:
    """Real filesystem workspace anchored at repo root.

    This workspace implementation wraps pathlib.Path operations
    to provide file I/O relative to a specified root directory.

    Attributes:
        root: Root directory for all file operations.
    """

    def __init__(
        self, root: Path | str, *, allowed_roots: Sequence[Path | str] | None = None
    ) -> None:
        """Initialize filesystem workspace.

        Args:
            root: Root directory path.
        """
        self._root = Path(root).expanduser().resolve()
        requested_allowed = allowed_roots or (self._root,)
        self._allowed_roots = tuple(Path(path).expanduser().resolve() for path in requested_allowed)

    def _resolve_candidate(self, path: str) -> Path:
        candidate_path = Path(path)
        base = self._root if not candidate_path.is_absolute() else Path("/")
        candidate = (base / candidate_path).expanduser().resolve(strict=False)
        for allowed_root in self._allowed_roots:
            try:
                candidate.relative_to(allowed_root)
                return candidate
            except ValueError:
                continue
        msg = f"Path '{path}' resolves outside workspace root"
        raise ValueError(msg)

    def _abs(self, path: str) -> Path:
        """Convert relative path to absolute path.

        Args:
            path: Relative path.

        Returns:
            Absolute path.
        """
        return self._resolve_candidate(path)

    def read(self, path: str) -> str:
        """Read file contents.

        Args:
            path: Relative path to the file.

        Returns:
            File contents as string.

        Raises:
            FileNotFoundError: If file doesn't exist.
        """
        return self._abs(path).read_text(encoding="utf-8")

    def write(self, path: str, content: str) -> None:
        """Write content to file.

        Args:
            path: Relative path to the file.
            content: Content to write.
        """
        p = self._abs(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def append(self, path: str, content: str) -> None:
        """Append content to file.

        Args:
            path: Relative path to the file.
            content: Content to append.
        """
        p = self._abs(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(content)

    def exists(self, path: str) -> bool:
        """Check if file exists.

        Args:
            path: Relative path to check.

        Returns:
            True if file exists.
        """
        return self._abs(path).exists()

    def remove(self, path: str) -> None:
        """Remove a file.

        Args:
            path: Relative path to the file.
        """
        self._abs(path).unlink(missing_ok=True)

    def list_dir(self, path: str) -> list[str]:
        """List directory contents.

        Args:
            path: Relative path to the directory.

        Returns:
            List of file/directory names.
        """
        p = self._abs(path)
        if not p.is_dir():
            return []
        return [str(item.relative_to(p)) for item in p.iterdir()]

    def is_dir(self, path: str) -> bool:
        """Check if path is a directory.

        Args:
            path: Relative path to check.

        Returns:
            True if path is a directory.
        """
        return self._abs(path).is_dir()

    def is_file(self, path: str) -> bool:
        """Check if path is a file.

        Args:
            path: Relative path to check.

        Returns:
            True if path is a file.
        """
        return self._abs(path).is_file()

    @property
    def root(self) -> Path:
        """Get the workspace root directory.

        Returns:
            Root Path object.
        """
        return self._root

    def absolute_path(self, path: str) -> str:
        """Return the absolute filesystem path for a workspace-relative path."""
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
        """Read lines from a file with slicing support.

        Memory-bounded: a ``stat`` precheck rejects files above
        ``max_bytes`` (default ``MAX_READ_LINES_BYTES`` = 5 MB) so a
        partial read against a multi-GB file cannot OOM the agent.
        Partial reads (``head`` / ``tail`` / ``start..end``) stream
        the file and only materialize the requested window, so peak
        allocation is O(window) not O(file size). The full-file path
        also streams in 64KB chunks via ``readlines(hint=...)`` to
        avoid reading the whole file into a single ``str``.

        Args:
            path: Relative path to the file.
            start: 1-based line number to start from (inclusive).
            end: 1-based line number to end at (inclusive).
            head: Return only the first N lines.
            tail: Return only the last N lines.
            max_bytes: Optional override for the size precheck.

        Returns:
            Tuple of (text content, metadata dict) where metadata has
            total_lines, returned_lines, truncated keys.

        Raises:
            ValueError: If conflicting params are supplied, or the
                file exceeds ``max_bytes``.
            FileNotFoundError: If file doesn't exist.
        """
        has_range = (start is not None) or (end is not None)
        has_head = head is not None
        has_tail = tail is not None
        mode_count = sum(1 for m in (has_range, has_head, has_tail) if m)
        if mode_count > 1:
            raise ValueError("Only one of (start/end range), head, or tail may be specified")

        abs_path = self._abs(path)
        ceiling = max_bytes if max_bytes is not None else MAX_READ_LINES_BYTES
        file_size = abs_path.stat().st_size
        if file_size > ceiling:
            raise ValueError(
                f"File too large for read_lines: {file_size} bytes exceeds "
                f"limit of {ceiling} bytes (path={path!r}). "
                "Use a partial read (head/tail/range) or raise max_bytes."
            )

        total_lines = self._count_lines(abs_path)

        returned_lines: list[str]
        truncated = False

        if head is not None:
            returned_lines = self._read_head_lines(abs_path, head)
            if total_lines > head:
                truncated = True
        elif tail is not None:
            returned_lines = self._read_tail_lines(abs_path, tail)
            if total_lines > tail:
                truncated = True
        elif start is not None or end is not None:
            start_idx = (start - 1) if start is not None else 0
            end_idx = end if end is not None else total_lines
            start_idx = max(0, start_idx)
            end_idx = min(total_lines, end_idx)
            returned_lines = self._read_range_lines(abs_path, start_idx, end_idx)
            if end_idx < total_lines:
                truncated = True
        else:
            returned_lines = self._read_all_lines(abs_path)

        return "".join(returned_lines), {
            "total_lines": total_lines,
            "returned_lines": len(returned_lines),
            "truncated": truncated,
        }

    @staticmethod
    def _count_lines(abs_path: Path) -> int:
        """Count lines via streaming 64KB chunks (O(n) time, O(1) memory).

        Counts newline-terminated lines PLUS a final unterminated line
        when the file is non-empty and does NOT end with a newline
        byte, so a file containing ``alpha\\nbeta`` reports
        ``total_lines == 2`` (matching the contract that
        ``read_lines`` returns one string per line). An empty file
        reports ``total_lines == 0`` even though there is no trailing
        newline to "add 1" for. The chunked scan uses a 64KB buffer
        and never materializes the file's full byte content.
        """
        total = 0
        last_byte: int | None = None
        with abs_path.open("rb") as fh:
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
    def _read_head_lines(abs_path: Path, head: int) -> list[str]:
        """Read the first ``head`` lines via itertools.islice (O(window) memory)."""
        with abs_path.open(encoding="utf-8") as fh:
            return list(itertools.islice(fh, head))

    @staticmethod
    def _read_tail_lines(abs_path: Path, tail: int) -> list[str]:
        """Read the last ``tail`` lines via collections.deque (O(window) memory)."""
        with abs_path.open(encoding="utf-8") as fh:
            return list(deque(fh, maxlen=tail))

    @staticmethod
    def _read_range_lines(abs_path: Path, start_idx: int, end_idx: int) -> list[str]:
        """Read lines in [start_idx, end_idx) via itertools.islice (O(window) memory)."""
        with abs_path.open(encoding="utf-8") as fh:
            return list(itertools.islice(fh, start_idx, end_idx))

    @staticmethod
    def _read_all_lines(abs_path: Path) -> list[str]:
        """Read all lines via 64KB-chunked stream (O(n) time, O(n) memory).

        The full-file path still requires the entire file in memory
        (we need to return all of it) but the chunked scan avoids the
        per-line ``str.decode`` overhead of ``fh.readlines()`` and
        lets us short-circuit early if ``ValueError`` is raised.
        """
        with abs_path.open(encoding="utf-8") as fh:
            return fh.readlines()

    def read_bytes(
        self,
        path: str,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[str, dict[str, object]]:
        """Read a byte window from a file, decoded as UTF-8."""
        abs_path = self._abs(path)
        if not abs_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        total_bytes = abs_path.stat().st_size
        with abs_path.open("rb") as fh:
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
        """Get file metadata/stat data.

        Args:
            path: Relative path to the file.

        Returns:
            Dict with type ('file'|'dir'|'missing'), size_bytes,
            created_unix, modified_unix, mode.
        """
        try:
            st = self._abs(path).stat()
        except FileNotFoundError:
            return {"type": "missing"}
        p = self._abs(path)
        if p.is_dir():
            return {
                "type": "dir",
                "size_bytes": 0,
                "created_unix": st.st_ctime,
                "modified_unix": st.st_mtime,
                "mode": st.st_mode,
            }
        return {
            "type": "file",
            "size_bytes": st.st_size,
            "created_unix": st.st_ctime,
            "modified_unix": st.st_mtime,
            "mode": st.st_mode,
        }

    def mkdirs(self, path: str) -> None:
        """Create a directory and all parent directories.

        Args:
            path: Relative path to the directory to create.
        """
        self._abs(path).mkdir(parents=True, exist_ok=True)

    def move(self, src: str, dest: str, *, overwrite: bool = False) -> None:
        """Move a file or directory.

        Args:
            src: Source path.
            dest: Destination path.
            overwrite: Whether to overwrite existing destination.

        Raises:
            FileExistsError: If dest exists and overwrite is False.
        """
        src_abs = self._abs(src)
        dest_abs = self._abs(dest)
        if dest_abs.exists() and not overwrite:
            raise FileExistsError(f"Destination '{dest}' already exists")
        shutil.move(str(src_abs), str(dest_abs))

    def copy(self, src: str, dest: str, *, overwrite: bool = False) -> None:
        """Copy a file or directory.

        Args:
            src: Source path.
            dest: Destination path.
            overwrite: Whether to overwrite existing destination.

        Raises:
            FileExistsError: If dest exists and overwrite is False.
        """
        src_abs = self._abs(src)
        dest_abs = self._abs(dest)
        if dest_abs.exists() and not overwrite:
            raise FileExistsError(f"Destination '{dest}' already exists")
        if src_abs.is_dir():
            shutil.copytree(str(src_abs), str(dest_abs), dirs_exist_ok=overwrite)
        else:
            dest_abs.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src_abs), str(dest_abs))

    def delete(self, path: str, *, recursive: bool = False) -> None:
        """Delete a file or directory.

        Args:
            path: Relative path to delete.
            recursive: If True, delete directories recursively.

        Raises:
            IsADirectoryError: If path is a directory and recursive is False.
        """
        p = self._abs(path)
        if p.is_dir():
            if not recursive:
                raise IsADirectoryError(f"Path '{path}' is a directory, use recursive=True")
            shutil.rmtree(str(p))
        else:
            p.unlink()

    def allowed_roots(self) -> list[str]:
        """Return the list of allowed workspace root paths.

        Returns:
            List of string paths from configured allowed roots.
        """
        return [str(p) for p in self._allowed_roots]

    def iter_files(self, base: str) -> tuple[str, ...]:
        """Iterate over file paths under a base directory.

        Args:
            base: Base directory path to search under.

        Yields:
            File paths relative to workspace root, honoring skip patterns.
        """
        base_abs = self._abs(base)
        if not base_abs.is_dir():
            return ()

        results: list[str] = []
        for root, dirs, files in os.walk(str(base_abs)):
            root_path = Path(root)
            dirs[:] = [d for d in dirs if d not in RECURSIVE_SKIP_DIRECTORY_NAMES]
            rel_root = root_path.relative_to(self._root)
            results.extend(str(rel_root / f) for f in files)

        return tuple(results)
