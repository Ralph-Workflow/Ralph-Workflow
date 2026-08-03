"""Production filesystem workspace.

This module provides the FsWorkspace implementation that
wraps pathlib.Path operations for real filesystem access.
"""

from __future__ import annotations

import os
import shutil
from collections import deque
from pathlib import Path
from stat import S_ISDIR
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.workspace._snapshot import WorkspaceSnapshot
from ralph.workspace.skip import RECURSIVE_SKIP_DIRECTORY_NAMES

#: Default maximum file size in bytes for bounded Workspace content reads.
#: A ``stat`` precheck rejects full-file reads above this ceiling BEFORE we
#: open them so an agent request cannot OOM the process. It matches
#: ``FULL_READ_DEFAULT_MAX_BYTES`` in ``ralph/mcp/tools/workspace/_utils.py``.
MAX_READ_BYTES: int = 5_000_000
#: Backward-compatible name for the line-oriented Workspace read ceiling.
MAX_READ_LINES_BYTES: int = MAX_READ_BYTES

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
        self,
        root: Path | str,
        *,
        allowed_roots: Sequence[Path | str] | None = None,
        backend: FileBackend = DEFAULT_FILE_BACKEND,
    ) -> None:
        """Initialize filesystem workspace.

        Args:
            root: Root directory path.
            allowed_roots: Optional sequence of additional allowed root paths.
            backend: FileBackend used for the idempotent ``write`` guard so a
                fake backend can intercept both the parent-directory creation
                and the write itself (no real I/O under test).
        """
        self._root = Path(root).expanduser().resolve()
        requested_allowed = allowed_roots or (self._root,)
        self._allowed_roots = tuple(Path(path).expanduser().resolve() for path in requested_allowed)
        self._backend = backend
        # Optional ExploreIndex handle attached by the production
        # session bridge; ``None`` keeps the legacy contract.
        self.explore_index: object | None = None

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
        return self._backend.read_text(self._abs(path), encoding="utf-8")

    def snapshot(self, path: str, *, max_bytes: int | None = None) -> WorkspaceSnapshot:
        """Return metadata and text from a single open/read observation.

        The file descriptor stat and bytes are obtained together, avoiding a
        separate metadata probe followed by a second open in request handlers.
        """
        abs_path = self._abs(path)
        try:
            with abs_path.open("rb") as fh:
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
        """Write content to file.

        Skips the physical write when the target already contains
        byte-identical content, so re-emitting an unchanged file does
        not advance the file's mtime or generate an additional
        fseventsd notification. The post-condition "file contains
        ``content``" always holds: any read uncertainty or content
        mismatch falls through to a real write.

        Args:
            path: Relative path to the file.
            content: Content to write.
        """
        p = self._abs(path)
        write_text_if_changed(
            self._backend,
            p,
            content,
            encoding="utf-8",
            prepare_write=lambda: self._backend.mkdir(p.parent, parents=True, exist_ok=True),
        )

    def append(self, path: str, content: str) -> None:
        """Append content to file.

        Args:
            path: Relative path to the file.
            content: Content to append.
        """
        p = self._abs(path)
        # filesystem-write-ok: caller-requested append preserves the live stream contract
        p.parent.mkdir(parents=True, exist_ok=True)
        # filesystem-write-ok: caller-requested append preserves the live stream contract
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
        self._backend.unlink(self._abs(path), missing_ok=True)

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

        Memory-bounded: a ``stat`` precheck rejects only unbounded full-file
        reads above ``max_bytes`` (default ``MAX_READ_LINES_BYTES`` = 5 MB).
        Partial reads (``head`` / ``tail`` / ``start..end``) remain available
        for larger files because they stream
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
        if mode_count == 0 and file_size > ceiling:
            raise ValueError(
                f"File too large for read_lines: {file_size} bytes exceeds "
                f"limit of {ceiling} bytes (path={path!r}). "
                "Use a partial read (head/tail/range) or raise max_bytes."
            )

        returned_lines, total_lines = self._read_lines_once(
            abs_path,
            start=start,
            end=end,
            head=head,
            tail=tail,
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
    def _read_lines_once(
        abs_path: Path,
        *,
        start: int | None,
        end: int | None,
        head: int | None,
        tail: int | None,
    ) -> tuple[list[str], int]:
        """Read and count a requested line window from one file observation.

        The stream is opened once so metadata and returned content describe the
        same observation. Head and range requests retain only their requested
        lines; tail uses a bounded deque. Full reads retain all lines because
        the public result necessarily contains all lines.
        """
        if head is not None:
            selected: list[str] | deque[str] = []
        elif tail is not None:
            selected = deque(maxlen=tail)
        elif start is not None or end is not None:
            selected = []
        else:
            selected = []

        total_lines = 0
        with abs_path.open(encoding="utf-8") as fh:
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
        """Read a UTF-8 byte window without loading an unbounded full file.

        A bounded window may be read from a larger file. Negative offsets and
        limits are invalid, and the requested byte range is rejected when it
        exceeds the configured ceiling before the file is opened.
        """
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
        self._backend.mkdir(self._abs(path), parents=True, exist_ok=True)

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
        # filesystem-write-ok: explicit user-requested workspace move preserves source bytes and metadata
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
            # filesystem-write-ok: explicit user-requested workspace directory copy preserves the tree contract
            shutil.copytree(str(src_abs), str(dest_abs), dirs_exist_ok=overwrite)
        else:
            self._backend.mkdir(dest_abs.parent, parents=True, exist_ok=True)
            # filesystem-write-ok: explicit user-requested workspace copy preserves source bytes and metadata
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
            # filesystem-write-ok: explicit user-requested recursive workspace deletion
            shutil.rmtree(str(p))
        else:
            # filesystem-write-ok: explicit user-requested workspace file deletion
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
        if not base_abs.is_dir() or self._is_skipped_traversal_base(base_abs):
            return ()

        results: list[str] = []
        # filesystem-read-ok: canonical Workspace traversal applies the shared recursive skip set.
        for root, dirs, files in os.walk(str(base_abs)):
            root_path = Path(root)
            dirs[:] = [d for d in dirs if d not in RECURSIVE_SKIP_DIRECTORY_NAMES]
            rel_root = root_path.relative_to(self._root)
            results.extend(str(rel_root / f) for f in files)

        return tuple(results)

    def _is_skipped_traversal_base(self, base_abs: Path) -> bool:
        """Return whether a requested base lies inside an excluded generated tree."""
        try:
            relative_parts = base_abs.relative_to(self._root).parts
        except ValueError:
            return False
        return any(part in RECURSIVE_SKIP_DIRECTORY_NAMES for part in relative_parts)
