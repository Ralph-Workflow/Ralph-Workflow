from __future__ import annotations

from pathlib import Path


class _ArtifactWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def absolute_path(self, path: str) -> str:
        return str(self.root / path)
