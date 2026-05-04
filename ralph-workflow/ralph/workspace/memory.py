"""In-memory workspace for testing.

This module provides the MemoryWorkspace implementation that
stores file contents in memory for test isolation.
"""

from __future__ import annotations

from pathlib import PurePosixPath


class MemoryWorkspace:
    """In-memory workspace for test isolation.

    This workspace stores all file contents in a dictionary,
    making it suitable for unit testing without filesystem
    operations.

    All paths are normalized to POSIX-style relative paths.
    """

    def __init__(self, root: str = "/workspace") -> None:
        """Initialize empty in-memory workspace."""
        self._storage: dict[str, str] = {}
        self._dirs: set[str] = {""}  # Root is always present
        self._root = PurePosixPath(root)

    def _normalize(self, path: str) -> str:
        """Normalize path to POSIX-style relative path.

        Args:
            path: Input path.

        Returns:
            Normalized path.
        """
        normalized = str(PurePosixPath(path))
        return "" if normalized == "." else normalized

    def _ensure_parent(self, path: str) -> None:
        """Ensure parent directory exists.

        Args:
            path: File path.
        """
        p = PurePosixPath(path)
        if p.parent == PurePosixPath("."):
            return
        for parent in reversed(p.parents[:-1]):
            normalized_parent = self._normalize(str(parent))
            if normalized_parent:
                self._dirs.add(normalized_parent)

    def read(self, path: str) -> str:
        """Read file contents.

        Args:
            path: Relative path to the file.

        Returns:
            File contents as string.

        Raises:
            FileNotFoundError: If file doesn't exist.
        """
        normalized = self._normalize(path)
        if normalized not in self._storage:
            msg = f"File not found: {path}"
            raise FileNotFoundError(msg)
        return self._storage[normalized]

    def write(self, path: str, content: str) -> None:
        """Write content to file.

        Args:
            path: Relative path to the file.
            content: Content to write.
        """
        normalized = self._normalize(path)
        self._ensure_parent(normalized)
        self._storage[normalized] = content

    def append(self, path: str, content: str) -> None:
        """Append content to file.

        Args:
            path: Relative path to the file.
            content: Content to append.
        """
        normalized = self._normalize(path)
        self._ensure_parent(normalized)
        if normalized in self._storage:
            self._storage[normalized] += content
        else:
            self._storage[normalized] = content

    def exists(self, path: str) -> bool:
        """Check if file exists.

        Args:
            path: Relative path to check.

        Returns:
            True if file exists.
        """
        return self._normalize(path) in self._storage

    def remove(self, path: str) -> None:
        """Remove a file.

        Args:
            path: Relative path to the file.
        """
        normalized = self._normalize(path)
        self._storage.pop(normalized, None)

    def list_dir(self, path: str) -> list[str]:
        """List directory contents.

        Args:
            path: Relative path to the directory.

        Returns:
            List of file/directory names.
        """
        normalized = self._normalize(path)
        if normalized and not normalized.endswith("/"):
            normalized += "/"

        result: list[str] = []
        for key in self._storage:
            if key == normalized:
                continue
            if normalized == "" or key.startswith(normalized):
                remainder = key[len(normalized) :]
                first_part = remainder.split("/")[0]
                if first_part and first_part not in result:
                    result.append(first_part)

        # Also check directories
        for d in self._dirs:
            if normalized == "" or d.startswith(normalized):
                if d == normalized:
                    continue
                if normalized == "" or d.startswith(normalized):
                    remainder = d[len(normalized) :].lstrip("/")
                    first_part = remainder.split("/")[0]
                    if first_part and first_part not in result:
                        result.append(first_part)

        return sorted(result)

    def is_dir(self, path: str) -> bool:
        """Check if path is a directory.

        Args:
            path: Relative path to check.

        Returns:
            True if path is a directory.
        """
        normalized = self._normalize(path)
        return normalized in self._dirs

    def is_file(self, path: str) -> bool:
        """Check if path is a file.

        Args:
            path: Relative path to check.

        Returns:
            True if path is a file.
        """
        return self.exists(path)

    def clear(self) -> None:
        """Clear all stored contents."""
        self._storage.clear()
        self._dirs = {""}

    def create_dir(self, path: str) -> None:
        """Create a directory.

        Args:
            path: Relative path to the directory.
        """
        normalized = self._normalize(path)
        if normalized:
            self._dirs.add(normalized)

    def absolute_path(self, path: str) -> str:
        """Return an absolute-like path string for the workspace."""
        return str(self._root / self._normalize(path))

    def read_lines(
        self,
        path: str,
        *,
        start: int | None = None,
        end: int | None = None,
        head: int | None = None,
        tail: int | None = None,
    ) -> tuple[str, dict[str, object]]:
        """Read lines from a file with slicing support.

        Args:
            path: Relative path to the file.
            start: 1-based line number to start from (inclusive).
            end: 1-based line number to end at (inclusive).
            head: Return only the first N lines.
            tail: Return only the last N lines.

        Returns:
            Tuple of (text content, metadata dict) where metadata has
            total_lines, returned_lines, truncated keys.

        Raises:
            ValueError: If conflicting params are supplied.
            FileNotFoundError: If file doesn't exist.
        """
        # Count how many mode groups are specified
        has_range = (start is not None) or (end is not None)
        has_head = head is not None
        has_tail = tail is not None
        mode_count = sum(1 for m in (has_range, has_head, has_tail) if m)
        if mode_count > 1:
            raise ValueError(
                "Only one of (start/end range), head, or tail may be specified"
            )

        content = self.read(path)
        all_lines = content.splitlines(keepends=True)
        total_lines = len(all_lines)
        returned_lines: list[str]
        truncated = False

        if head is not None:
            returned_lines = all_lines[:head]
            if total_lines > head:
                truncated = True
        elif tail is not None:
            returned_lines = all_lines[-tail:]
            if total_lines > tail:
                truncated = True
        elif start is not None or end is not None:
            start_idx = (start - 1) if start is not None else 0
            end_idx = end if end is not None else total_lines
            start_idx = max(0, start_idx)
            end_idx = min(total_lines, end_idx)
            returned_lines = all_lines[start_idx:end_idx]
            if end_idx < total_lines:
                truncated = True
        else:
            returned_lines = all_lines

        return "".join(returned_lines), {
            "total_lines": total_lines,
            "returned_lines": len(returned_lines),
            "truncated": truncated,
        }

    def read_bytes(
        self,
        path: str,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[str, dict[str, object]]:
        """Read a byte window from a file, decoded as UTF-8."""
        normalized = self._normalize(path)
        if normalized not in self._storage:
            raise FileNotFoundError(f"File not found: {path}")
        raw = self._storage[normalized].encode("utf-8")
        total_bytes = len(raw)
        sliced = raw[offset : offset + limit] if limit is not None else raw[offset:]
        returned_bytes = len(sliced)
        truncated = (offset + returned_bytes) < total_bytes
        text = sliced.decode("utf-8")
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
        normalized = self._normalize(path)
        if normalized in self._dirs:
            return {
                "type": "dir",
                "size_bytes": 0,
                "created_unix": None,
                "modified_unix": None,
                "mode": None,
            }
        if normalized in self._storage:
            return {
                "type": "file",
                "size_bytes": len(self._storage[normalized]),
                "created_unix": None,
                "modified_unix": None,
                "mode": None,
            }
        return {"type": "missing"}

    def mkdirs(self, path: str) -> None:
        """Create a directory and all parent directories.

        Args:
            path: Relative path to the directory to create.
        """
        normalized = self._normalize(path)
        if normalized:
            parts = normalized.split("/")
            for i in range(len(parts)):
                self._dirs.add("/".join(parts[: i + 1]))

    def move(self, src: str, dest: str, *, overwrite: bool = False) -> None:
        """Move a file or directory.

        Args:
            src: Source path.
            dest: Destination path.
            overwrite: Whether to overwrite existing destination.

        Raises:
            FileExistsError: If dest exists and overwrite is False.
        """
        src_norm = self._normalize(src)
        dest_norm = self._normalize(dest)
        if (dest_norm in self._storage or dest_norm in self._dirs) and not overwrite:
            raise FileExistsError(f"Destination '{dest}' already exists")
        if src_norm in self._storage:
            self._storage[dest_norm] = self._storage.pop(src_norm)
        elif src_norm in self._dirs:
            self._dirs.discard(src_norm)
            self._dirs.add(dest_norm)
        else:
            raise FileNotFoundError(f"Source '{src}' not found")

    def copy(self, src: str, dest: str, *, overwrite: bool = False) -> None:
        """Copy a file or directory.

        Args:
            src: Source path.
            dest: Destination path.
            overwrite: Whether to overwrite existing destination.

        Raises:
            FileExistsError: If dest exists and overwrite is False.
        """
        src_norm = self._normalize(src)
        dest_norm = self._normalize(dest)
        if (dest_norm in self._storage or dest_norm in self._dirs) and not overwrite:
            raise FileExistsError(f"Destination '{dest}' already exists")
        if src_norm in self._storage:
            self._storage[dest_norm] = self._storage[src_norm]
        elif src_norm in self._dirs:
            self._dirs.add(dest_norm)
        else:
            raise FileNotFoundError(f"Source '{src}' not found")

    def delete(self, path: str, *, recursive: bool = False) -> None:
        """Delete a file or directory.

        Args:
            path: Relative path to delete.
            recursive: If True, delete directories recursively.

        Raises:
            IsADirectoryError: If path is a directory and recursive is False.
        """
        normalized = self._normalize(path)
        if normalized in self._dirs:
            if not recursive:
                raise IsADirectoryError(f"Path '{path}' is a directory, use recursive=True")
            self._dirs.discard(normalized)
            self._storage.pop(normalized, None)
        elif normalized in self._storage:
            del self._storage[normalized]
        else:
            raise FileNotFoundError(f"Path '{path}' not found")

    def allowed_roots(self) -> list[str]:
        """Return the list of allowed workspace root paths.

        Returns:
            List of string paths from configured allowed roots.
        """
        return [str(self._root)]

    def iter_files(self, base: str) -> tuple[str, ...]:
        """Iterate over file paths under a base directory.

        Args:
            base: Base directory path to search under.

        Yields:
            File paths relative to workspace root, honoring skip patterns.
        """
        normalized = self._normalize(base)
        if normalized and not normalized.endswith("/"):
            normalized += "/"

        skip_names = frozenset({
            ".git", ".hg", ".mypy_cache", ".pytest_cache", ".ruff_cache",
            ".svn", ".venv", "__pycache__", "node_modules", "target",
        })

        results: list[str] = []
        for key in self._storage:
            if normalized == "" or key.startswith(normalized):
                remainder = key[len(normalized) :]
                parts = remainder.split("/")
                if len(parts) == 1 or parts[0] not in skip_names:
                    results.append(key)

        return tuple(results)
