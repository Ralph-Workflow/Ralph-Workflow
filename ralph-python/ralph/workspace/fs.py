"""Production filesystem workspace.

This module provides the FsWorkspace implementation that
wraps pathlib.Path operations for real filesystem access.
"""

from __future__ import annotations

from pathlib import Path


class FsWorkspace:
    """Real filesystem workspace anchored at repo root.

    This workspace implementation wraps pathlib.Path operations
    to provide file I/O relative to a specified root directory.

    Attributes:
        root: Root directory for all file operations.
    """

    def __init__(self, root: Path | str) -> None:
        """Initialize filesystem workspace.

        Args:
            root: Root directory path.
        """
        self._root = Path(root)

    def _abs(self, path: str) -> Path:
        """Convert relative path to absolute path.

        Args:
            path: Relative path.

        Returns:
            Absolute path.
        """
        return self._root / path

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
