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

    def __init__(self) -> None:
        """Initialize empty in-memory workspace."""
        self._storage: dict[str, str] = {}
        self._dirs: set[str] = {""}  # Root is always present

    def _normalize(self, path: str) -> str:
        """Normalize path to POSIX-style relative path.

        Args:
            path: Input path.

        Returns:
            Normalized path.
        """
        return str(PurePosixPath(path))

    def _ensure_parent(self, path: str) -> None:
        """Ensure parent directory exists.

        Args:
            path: File path.
        """
        p = PurePosixPath(path)
        if p.parent != PurePosixPath("."):
            self._dirs.add(str(p.parent))

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
                remainder = key[len(normalized):]
                first_part = remainder.split("/")[0]
                if first_part and first_part not in result:
                    result.append(first_part)

        # Also check directories
        for d in self._dirs:
            if normalized == "" or d.startswith(normalized):
                if d == normalized:
                    continue
                if normalized == "" or d.startswith(normalized):
                    remainder = d[len(normalized):].lstrip("/")
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
