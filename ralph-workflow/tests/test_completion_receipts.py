"""Tests for run-scoped artifact submission receipts.

A receipt is the authoritative, drift-proof signal that an artifact of a given
type was persisted during a given run. The submission handler writes it; the
completion gate reads it. The gate never recomputes a storage path, so a receipt
keyed on ``(run_id, artifact_type)`` cannot disagree with where the artifact
actually landed.
"""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.artifacts.completion_receipts import (
    artifact_receipt_present,
    clear_run_receipts,
    delete_artifact_receipt,
    write_artifact_receipt,
)


class FakeFileBackend:
    """In-memory FileBackend double (no real I/O, per test policy)."""

    def __init__(self) -> None:
        self.files: dict[Path, str] = {}
        self.directories: set[Path] = set()

    def exists(self, path: Path) -> bool:
        return path in self.files or path in self.directories

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        self.directories.add(path)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        return self.files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        self.files[path] = content

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self.files.pop(path, None)
            return
        del self.files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        suffix = pattern.replace("*", "")
        return [
            candidate
            for candidate in self.files
            if candidate.parent == path and candidate.name.endswith(suffix)
        ]


def test_receipt_present_after_write() -> None:
    backend = FakeFileBackend()
    ws = Path("/ws")
    write_artifact_receipt(ws, "run-1", "commit_message", backend=backend)
    assert artifact_receipt_present(ws, "run-1", "commit_message", backend=backend) is True


def test_receipt_absent_before_write() -> None:
    backend = FakeFileBackend()
    assert (
        artifact_receipt_present(Path("/ws"), "run-1", "commit_message", backend=backend) is False
    )


def test_receipt_keyed_by_artifact_type() -> None:
    backend = FakeFileBackend()
    ws = Path("/ws")
    write_artifact_receipt(ws, "run-1", "commit_message", backend=backend)
    assert artifact_receipt_present(ws, "run-1", "plan", backend=backend) is False


def test_receipt_keyed_by_run_id() -> None:
    backend = FakeFileBackend()
    ws = Path("/ws")
    write_artifact_receipt(ws, "run-1", "commit_message", backend=backend)
    assert artifact_receipt_present(ws, "run-2", "commit_message", backend=backend) is False


def test_clear_run_receipts_removes_receipt() -> None:
    backend = FakeFileBackend()
    ws = Path("/ws")
    write_artifact_receipt(ws, "run-1", "commit_message", backend=backend)
    clear_run_receipts(ws, "run-1", backend=backend)
    assert artifact_receipt_present(ws, "run-1", "commit_message", backend=backend) is False


def test_delete_artifact_receipt_removes_single_receipt() -> None:
    backend = FakeFileBackend()
    ws = Path("/ws")
    write_artifact_receipt(ws, "run-1", "commit_message", backend=backend)
    write_artifact_receipt(ws, "run-1", "development_result", backend=backend)
    delete_artifact_receipt(ws, "run-1", "commit_message", backend=backend)
    assert artifact_receipt_present(ws, "run-1", "commit_message", backend=backend) is False
    assert artifact_receipt_present(ws, "run-1", "development_result", backend=backend) is True


def test_delete_artifact_receipt_is_idempotent() -> None:
    backend = FakeFileBackend()
    ws = Path("/ws")
    # Must not raise when no receipt exists (rollback of a failed first op).
    delete_artifact_receipt(ws, "run-1", "commit_message", backend=backend)


def test_clear_run_receipts_scoped_to_run() -> None:
    backend = FakeFileBackend()
    ws = Path("/ws")
    write_artifact_receipt(ws, "run-1", "commit_message", backend=backend)
    write_artifact_receipt(ws, "run-2", "commit_message", backend=backend)
    clear_run_receipts(ws, "run-1", backend=backend)
    assert artifact_receipt_present(ws, "run-2", "commit_message", backend=backend) is True
