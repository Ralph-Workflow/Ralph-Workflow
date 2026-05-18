from __future__ import annotations

from pathlib import Path


class _Workspace:
    def __init__(self, root: Path) -> None:
        self._root = root

    def absolute_path(self, path: str) -> str:
        return str((self._root / path).resolve())
