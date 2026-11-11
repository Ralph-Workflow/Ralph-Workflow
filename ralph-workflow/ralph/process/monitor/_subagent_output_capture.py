
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class SubagentOutputCapture(Protocol):

    def read_lines(self, worker_id: str) -> list[str]:
        ...


class FileSubagentOutputCapture:

    def __init__(self, path: str, *, encoding: str = "utf-8") -> None:
        self._path = Path(path)
        self._encoding = encoding
        self._position = 0

    def read_lines(self, worker_id: str) -> list[str]:
        try:
            size = self._path.stat().st_size
        except OSError:
            return []

        if size < self._position:
            self._position = 0

        try:
            with self._path.open(encoding=self._encoding, errors="surrogateescape") as fh:
                fh.seek(self._position)
                data = fh.read()
                self._position = fh.tell()
        except OSError:
            return []

        if not data:
            return []

        if not data.endswith("\n"):
            last_nl = data.rfind("\n")
            encoded_tail = data.encode(self._encoding, errors="surrogateescape")
            if last_nl == -1:
                self._position -= len(encoded_tail)
                return []
            encoded_partial = data[last_nl + 1 :].encode(self._encoding, errors="surrogateescape")
            self._position -= len(encoded_partial)
            data = data[: last_nl + 1]

        return [line for line in data.splitlines() if line]
