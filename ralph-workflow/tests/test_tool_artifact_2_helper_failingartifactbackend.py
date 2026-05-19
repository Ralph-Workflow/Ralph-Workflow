from __future__ import annotations

from typing import TYPE_CHECKING

from tests.test_tool_artifact_2_helper_memorybackend import MemoryBackend

if TYPE_CHECKING:
    from pathlib import Path


class FailingArtifactBackend(MemoryBackend):
    def __init__(self, failing_path: Path, *, message: str = "artifact store unavailable") -> None:
        super().__init__()
        self._failing_path = failing_path
        self._message = message

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        if path == self._failing_path:
            raise OSError(self._message)
        super().write_text(path, content, encoding=encoding)
