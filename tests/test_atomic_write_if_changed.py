
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.mcp.artifacts.idempotent_write import atomic_write_text_if_changed

if TYPE_CHECKING:
    from collections.abc import Dict


class _ReplacingCountingBackend(FileBackend):

    def __init__(self) -> None:
        self._files: Dict[Path, str] = {}
        self.read_text_calls: list[Path] = []
        self.write_text_calls: list[tuple[Path, str]] = []
        self.replace_calls: list[tuple[Path, Path]] = []
        self.sync_directory_calls: list[Path] = []

    def exists(self, path: Path) -> bool:
        return path in self._files

    def mkdir(
        self, path: Path, *, parents: bool = False, exist_ok: bool = False
    ) -> None:
        del path, parents, exist_ok

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del encoding
        self.read_text_calls.append(path)
        return self._files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        del encoding
        self.write_text_calls.append((path, content))
        self._files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        self.replace_calls.append((source, destination))
        self._files[destination] = self._files.pop(source)

    def sync_directory(self, path: Path) -> None:
        self.sync_directory_calls.append(path)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self._files.pop(path, None)
            return
        del self._files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        del path, pattern
        return []


def test_atomic_write_concurrent_writers_publish_independent_final_bytes() -> None:
    backend = _ReplacingCountingBackend()
    destination = Path("/virtual-ws/concurrent.json")
    tmp_path = Path("/virtual-ws/concurrent.json.tmp")

    payloads = ("alpha-payload", "beta-payload", "gamma-payload")

    def _publish(payload: str) -> bool:
        return atomic_write_text_if_changed(
            backend,
            destination,
            payload,
            tmp_path=tmp_path,
        )

    with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
        results = list(pool.map(_publish, payloads))

    assert backend._files[destination] in payloads
    assert results.count(True) >= 1
    assert backend._files[destination] in {
        "alpha-payload",
        "beta-payload",
        "gamma-payload",
    }
    staging_paths = {call[0] for call in backend.write_text_calls}
    assert len(staging_paths) == len(backend.write_text_calls)


def test_atomic_write_concurrent_identical_writers_skip_redundant_publications() -> (
    None
):
    backend = _ReplacingCountingBackend()
    destination = Path("/virtual-ws/shared.json")
    tmp_path = Path("/virtual-ws/shared.json.tmp")
    backend._files[destination] = "stable"

    payloads = ("stable", "stable", "stable")

    def _publish(payload: str) -> bool:
        return atomic_write_text_if_changed(
            backend,
            destination,
            payload,
            tmp_path=tmp_path,
        )

    with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
        results = list(pool.map(_publish, payloads))

    assert results == [False, False, False]
    assert backend.write_text_calls == []
    assert backend.replace_calls == []
    assert backend._files[destination] == "stable"
