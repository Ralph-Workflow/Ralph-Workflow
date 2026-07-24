"""Transactional durability regressions for canonical artifact submission."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.artifacts import canonical_submit as canonical_submit_module
from ralph.mcp.artifacts import completion_receipts as completion_receipts_module
from ralph.mcp.artifacts import submit_artifact_canonical
from ralph.mcp.artifacts.completion_receipts import (
    ReceiptPersistenceError,
    artifact_receipt_present,
    write_artifact_receipt,
)
from ralph.mcp.artifacts.state_db import MISSING, RunStateDB
from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from tests.test_artifact_format_docs_memory_backend import MemoryBackend
from tests.test_canonical_artifact_submit import (
    COMMIT_MESSAGE,
    DEVELOPMENT_RESULT,
    _parsed,
    _submit,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def backend() -> MemoryBackend:
    return MemoryBackend()


def test_receipt_failure_propagates_from_current_persistence_module(
    tmp_path: Path,
    backend: MemoryBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_persistence(*_args: object, **_kwargs: object) -> None:
        raise ReceiptPersistenceError("durable receipt unavailable")

    monkeypatch.setattr(
        canonical_submit_module,
        "write_artifact_receipt",
        _raise_persistence,
    )

    with pytest.raises(ReceiptPersistenceError, match="durable receipt unavailable"):
        _submit(tmp_path, "commit_message", COMMIT_MESSAGE, backend=backend)

    assert not backend.exists(tmp_path / ".agent" / "artifacts" / "commit_message.md")
    assert not artifact_receipt_present(
        tmp_path,
        "run-1",
        "commit_message",
        backend=backend,
    )
    db = RunStateDB(tmp_path)
    try:
        assert db.get_completion_sentinel_hmac("run-1") is MISSING
    finally:
        db.close()


def test_receipt_partial_write_is_removed_when_submission_rolls_back(
    tmp_path: Path,
    backend: MemoryBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_write = canonical_submit_module.write_artifact_receipt

    def _write_then_raise(*args: object, **kwargs: object) -> None:
        original_write(*args, **kwargs)
        raise ReceiptPersistenceError("failure after durable receipt write")

    monkeypatch.setattr(
        canonical_submit_module,
        "write_artifact_receipt",
        _write_then_raise,
    )

    with pytest.raises(
        ReceiptPersistenceError,
        match="failure after durable receipt write",
    ):
        _submit(tmp_path, "commit_message", COMMIT_MESSAGE, backend=backend)

    assert not backend.exists(tmp_path / ".agent" / "artifacts" / "commit_message.md")
    assert not artifact_receipt_present(
        tmp_path,
        "run-1",
        "commit_message",
        backend=backend,
    )


def test_receipt_rollback_preserves_preexisting_receipt_for_restored_artifact(
    tmp_path: Path,
    backend: MemoryBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _submit(
        tmp_path,
        "development_result",
        DEVELOPMENT_RESULT,
        backend=backend,
        run_id="same-run",
    )
    original_write = canonical_submit_module.write_artifact_receipt
    updated = DEVELOPMENT_RESULT.replace(
        "Completed the Markdown migration.",
        "This replacement must roll back.",
    )

    def _write_then_raise(*args: object, **kwargs: object) -> None:
        original_write(*args, **kwargs)
        raise ReceiptPersistenceError("failure after replacing receipt")

    monkeypatch.setattr(
        canonical_submit_module,
        "write_artifact_receipt",
        _write_then_raise,
    )

    with pytest.raises(
        ReceiptPersistenceError,
        match="failure after replacing receipt",
    ):
        _submit(
            tmp_path,
            "development_result",
            updated,
            backend=backend,
            run_id="same-run",
        )

    assert artifact_receipt_present(
        tmp_path,
        "same-run",
        "development_result",
        backend=backend,
    )
    assert (
        backend.read_text(tmp_path / ".agent" / "artifacts" / "development_result.md")
        == DEVELOPMENT_RESULT
    )


def test_receipt_rollback_removes_preexisting_receipt_without_restored_artifact(
    tmp_path: Path,
    backend: MemoryBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_artifact_receipt(
        tmp_path,
        "run-1",
        "commit_message",
        backend=backend,
    )

    def _raise_persistence(*_args: object, **_kwargs: object) -> None:
        raise ReceiptPersistenceError("durable receipt unavailable")

    monkeypatch.setattr(
        canonical_submit_module,
        "write_artifact_receipt",
        _raise_persistence,
    )

    with pytest.raises(ReceiptPersistenceError, match="durable receipt unavailable"):
        _submit(tmp_path, "commit_message", COMMIT_MESSAGE, backend=backend)

    assert not backend.exists(tmp_path / ".agent" / "artifacts" / "commit_message.md")
    assert not artifact_receipt_present(
        tmp_path,
        "run-1",
        "commit_message",
        backend=backend,
    )


def test_receipt_failure_restores_previous_artifact_and_handoff(
    tmp_path: Path,
    backend: MemoryBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _submit(
        tmp_path,
        "development_result",
        DEVELOPMENT_RESULT,
        backend=backend,
        run_id="run-old",
    )
    updated = DEVELOPMENT_RESULT.replace(
        "Completed the Markdown migration.",
        "This replacement must roll back.",
    )

    def _raise_persistence(*_args: object, **_kwargs: object) -> None:
        raise ReceiptPersistenceError("durable receipt unavailable")

    monkeypatch.setattr(
        canonical_submit_module,
        "write_artifact_receipt",
        _raise_persistence,
    )

    with pytest.raises(ReceiptPersistenceError, match="durable receipt unavailable"):
        _submit(
            tmp_path,
            "development_result",
            updated,
            backend=backend,
            run_id="run-new",
        )

    assert (
        backend.read_text(tmp_path / ".agent" / "artifacts" / "development_result.md")
        == DEVELOPMENT_RESULT
    )
    assert backend.read_text(tmp_path / ".agent" / "DEVELOPMENT_RESULT.md") == DEVELOPMENT_RESULT
    assert not artifact_receipt_present(
        tmp_path,
        "run-new",
        "development_result",
        backend=backend,
    )


def test_receipt_failure_removes_history_created_by_failed_attempt(
    tmp_path: Path,
    backend: MemoryBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _submit(
        tmp_path,
        "development_result",
        DEVELOPMENT_RESULT,
        backend=backend,
        run_id="run-old",
    )

    def _raise_persistence(*_args: object, **_kwargs: object) -> None:
        raise ReceiptPersistenceError("durable receipt unavailable")

    monkeypatch.setattr(
        canonical_submit_module,
        "write_artifact_receipt",
        _raise_persistence,
    )
    deps = ArtifactHandlerDeps(
        backend=backend,
        history_enabled=True,
        now_iso=lambda: "2026-07-24T01:02:03+00:00",
    )

    with pytest.raises(ReceiptPersistenceError, match="durable receipt unavailable"):
        submit_artifact_canonical(
            workspace_root=tmp_path,
            artifact_type="development_result",
            parsed_content=_parsed("development_result", DEVELOPMENT_RESULT),
            markdown=DEVELOPMENT_RESULT,
            deps=deps,
            run_id="run-new",
        )

    history_dir = tmp_path / ".agent" / "artifacts" / "history" / "development_result"
    assert not backend.exists(history_dir / "20260724T010203_development_result.md")
    assert not backend.exists(history_dir / "20260724T010203_1_development_result.md")
    assert not backend.exists(history_dir / "index.md")


def test_interrupted_history_snapshot_does_not_leave_partial_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _MarkdownGlobBackend(MemoryBackend):
        def glob(self, path: Path, pattern: str) -> list[Path]:
            if pattern == "*.md":
                return [
                    candidate
                    for candidate in self._files
                    if candidate.parent == path and candidate.suffix == ".md"
                ]
            return super().glob(path, pattern)

    backend = _MarkdownGlobBackend()
    _submit(
        tmp_path,
        "development_result",
        DEVELOPMENT_RESULT,
        backend=backend,
        run_id="run-old",
    )
    history_dir = tmp_path / ".agent" / "artifacts" / "history" / "development_result"
    partial_archive = history_dir / "20260724T010203_development_result.md"

    def _partial_snapshot(
        artifact_dir: Path,
        workspace_root: Path,
        artifact_type: str,
        *,
        backend: MemoryBackend,
        now_iso: object,
    ) -> list[Path]:
        del artifact_dir, workspace_root, artifact_type, now_iso
        backend.mkdir(history_dir, parents=True, exist_ok=True)
        backend.write_text(partial_archive, DEVELOPMENT_RESULT)
        raise OSError("snapshot interrupted after archive write")

    monkeypatch.setattr(
        canonical_submit_module,
        "snapshot_current_artifact",
        _partial_snapshot,
    )
    deps = ArtifactHandlerDeps(
        backend=backend,
        history_enabled=True,
        now_iso=lambda: "2026-07-24T01:02:03+00:00",
    )

    with pytest.raises(OSError, match="snapshot interrupted"):
        submit_artifact_canonical(
            workspace_root=tmp_path,
            artifact_type="development_result",
            parsed_content=_parsed("development_result", DEVELOPMENT_RESULT),
            markdown=DEVELOPMENT_RESULT,
            deps=deps,
            run_id="run-new",
        )

    assert not backend.exists(partial_archive)
    assert (
        backend.read_text(tmp_path / ".agent" / "artifacts" / "development_result.md")
        == DEVELOPMENT_RESULT
    )


def test_receipt_db_failure_uses_legacy_durable_fallback(
    tmp_path: Path,
    backend: MemoryBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_sqlite(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        completion_receipts_module.RunStateDB,
        "upsert_receipt",
        _raise_sqlite,
        raising=True,
    )

    result = _submit(tmp_path, "commit_message", COMMIT_MESSAGE, backend=backend)

    legacy = tmp_path / ".agent" / "receipts" / "run-1" / "commit_message.json"
    assert result.receipt_path == legacy
    assert backend.exists(legacy)
    assert artifact_receipt_present(
        tmp_path,
        "run-1",
        "commit_message",
        backend=backend,
    )
