"""Workspace Protocol for file I/O abstraction.

This module defines the Workspace protocol that enables
test doubles and in-memory implementations for testing.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Workspace(Protocol):
    """File I/O abstraction enabling test doubles.

    This protocol defines the interface for file system operations.
    Implementations can be production (FsWorkspace) or test doubles
    (MemoryWorkspace).

    All paths are relative to the workspace root.
    """

    def read(self, path: str) -> str:
        """Read file contents.

        Args:
            path: Relative path to the file.

        Returns:
            File contents as string.

        Raises:
            FileNotFoundError: If file doesn't exist.
        """
        ...

    def write(self, path: str, content: str) -> None:
        """Write content to file.

        Args:
            path: Relative path to the file.
            content: Content to write.
        """
        ...

    def append(self, path: str, content: str) -> None:
        """Append content to file.

        Args:
            path: Relative path to the file.
            content: Content to append.
        """
        ...

    def exists(self, path: str) -> bool:
        """Check if file exists.

        Args:
            path: Relative path to check.

        Returns:
            True if file exists.
        """
        ...

    def remove(self, path: str) -> None:
        """Remove a file.

        Args:
            path: Relative path to the file.
        """
        ...

    def list_dir(self, path: str) -> list[str]:
        """List directory contents.

        Args:
            path: Relative path to the directory.

        Returns:
            List of file/directory names in the directory.
        """
        ...

    def is_dir(self, path: str) -> bool:
        """Check if path is a directory.

        Args:
            path: Relative path to check.

        Returns:
            True if path is a directory.
        """
        ...

    def is_file(self, path: str) -> bool:
        """Check if path is a file.

        Args:
            path: Relative path to check.

        Returns:
            True if path is a file.
        """
        ...

    def absolute_path(self, path: str) -> str:
        """Resolve a relative path to its absolute workspace path."""
        ...

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
        ...

    def stat(self, path: str) -> dict[str, object]:
        """Get file metadata/stat data.

        Args:
            path: Relative path to the file.

        Returns:
            Dict with type ('file'|'dir'|'missing'), size_bytes,
            created_unix, modified_unix, mode.
        """
        ...

    def mkdirs(self, path: str) -> None:
        """Create a directory and all parent directories.

        Args:
            path: Relative path to the directory to create.
        """
        ...

    def move(self, src: str, dest: str, *, overwrite: bool = False) -> None:
        """Move a file or directory.

        Args:
            src: Source path.
            dest: Destination path.
            overwrite: Whether to overwrite existing destination.
        """
        ...

    def copy(self, src: str, dest: str, *, overwrite: bool = False) -> None:
        """Copy a file or directory.

        Args:
            src: Source path.
            dest: Destination path.
            overwrite: Whether to overwrite existing destination.
        """
        ...

    def delete(self, path: str, *, recursive: bool = False) -> None:
        """Delete a file or directory.

        Args:
            path: Relative path to delete.
            recursive: If True, delete directories recursively.

        Raises:
            IsADirectoryError: If path is a directory and recursive is False.
        """
        ...

    def allowed_roots(self) -> list[str]:
        """Return the list of allowed workspace root paths.

        Returns:
            List of string paths from configured allowed roots.
        """
        ...

    def iter_files(self, base: str) -> tuple[str, ...]:
        """Iterate over file paths under a base directory.

        Args:
            base: Base directory path to search under.

        Yields:
            File paths relative to workspace root, honoring skip patterns.
        """
        ...
