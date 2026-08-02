"""Black-box regression coverage for durable auto-integration record publication."""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.pipeline.auto_integrate_record import IntegrationRecord, write_record


class _RecordBackend(FileBackend):
    """In-memory durable publication boundary that exposes observable mutations."""

    def __init__(self) -> None:
        self.files: dict[Path, bytes] = {}
        self.write_calls: list[tuple[Path, bytes]] = []
        self.replace_calls: list[tuple[Path, Path]] = []
        self.sync_calls: list[Path] = []
        self.mkdir_calls: list[Path] = []

    def exists(self, path: Path) -> bool:
        return path in self.files

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del parents, exist_ok
        self.mkdir_calls.append(path)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        return self.files[path].decode(encoding)

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        self.write_bytes(path, content.encode(encoding))

    def read_bytes(self, path: Path) -> bytes:
        return self.files[path]

    def write_bytes(self, path: Path, content: bytes) -> None:
        self.write_calls.append((path, content))
        self.files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        self.replace_calls.append((source, destination))
        self.files[destination] = self.files.pop(source)

    def sync_directory(self, path: Path) -> None:
        self.sync_calls.append(path)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self.files.pop(path, None)
        else:
            del self.files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        del path, pattern
        return []


def _record() -> IntegrationRecord:
    return IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature-before",
        pre_target_sha="target-before",
    )


def test_auto_integrate_record_regression_identical_replay_skips_publication_and_barrier() -> None:
    """S-2: replaying unchanged durable state makes no new filesystem mutations."""
    backend = _RecordBackend()
    root = Path("/virtual-workspace")
    record = _record()

    write_record(root, record, backend=backend)
    writes_after_first = list(backend.write_calls)
    replaces_after_first = list(backend.replace_calls)
    syncs_after_first = list(backend.sync_calls)
    mkdirs_after_first = list(backend.mkdir_calls)

    write_record(root, record, backend=backend)

    assert backend.write_calls == writes_after_first
    assert backend.replace_calls == replaces_after_first
    assert backend.sync_calls == syncs_after_first
    assert backend.mkdir_calls == mkdirs_after_first
    record_file = root / ".agent" / "auto_integrate_in_progress.json"
    assert backend.files[record_file] == record.model_dump_json().encode("utf-8")
