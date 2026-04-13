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
