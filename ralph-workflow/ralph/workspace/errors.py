
from __future__ import annotations

from pathlib import Path


class WorkspaceContextError(Exception):

    def __init__(self, file: Path, reason: str, fix: str | None = None) -> None:
        self.file = Path(file)
        self.reason = reason
        self.fix = fix or f"fix {self.file} then run ralph --check-config"
        if self.fix in self.reason:
            super().__init__(self.reason)
        else:
            super().__init__(f"{self.reason} ({self.fix})")


__all__ = ["WorkspaceContextError"]
