from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.artifacts import completion_receipts as receipts_module
from ralph.mcp.artifacts.completion_receipts import (
    ReceiptPersistenceError,
    _receipt_hmac,
    artifact_receipt_present,
    clear_run_receipts,
    delete_artifact_receipt,
    write_artifact_receipt,
)

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch


def test_receipt_present_after_write(tmp_path: Path) -> None:
    write_artifact_receipt(tmp_path, "run-1", "commit_message")
    assert artifact_receipt_present(tmp_path, "run-1", "commit_message") is True


def test_receipt_absent_before_write(tmp_path: Path) -> None:
    assert artifact_receipt_present(tmp_path, "run-1", "commit_message") is False


def test_receipt_keyed_by_artifact_type(tmp_path: Path) -> None:
    write_artifact_receipt(tmp_path, "run-1", "commit_message")
    assert artifact_receipt_present(tmp_path, "run-1", "plan") is False


def test_commit_receipt_stores_normalization_audit(tmp_path: Path) -> None:
    from ralph.mcp.artifacts.state_db import RunStateDB

    audit: dict[str, object] = {
        "confidence": "high",
        "transformations": ["canonicalized files"],
    }
    write_artifact_receipt(tmp_path, "run-1", "commit_message", normalization_audit=audit)
    db = RunStateDB(tmp_path)
    try:
        assert db.get_receipt_normalization_audit("run-1", "commit_message") == json.dumps(
            audit, sort_keys=True
        )
    finally:
        db.close()


def test_receipt_keyed_by_run_id(tmp_path: Path) -> None:
    write_artifact_receipt(tmp_path, "run-1", "commit_message")
    assert artifact_receipt_present(tmp_path, "run-2", "commit_message") is False


def test_clear_run_receipts_removes_receipt(tmp_path: Path) -> None:
    write_artifact_receipt(tmp_path, "run-1", "commit_message")
    clear_run_receipts(tmp_path, "run-1")
    assert artifact_receipt_present(tmp_path, "run-1", "commit_message") is False


def test_delete_artifact_receipt_removes_single_receipt(tmp_path: Path) -> None:
    write_artifact_receipt(tmp_path, "run-1", "commit_message")
    write_artifact_receipt(tmp_path, "run-1", "development_result")
    delete_artifact_receipt(tmp_path, "run-1", "commit_message")
    assert artifact_receipt_present(tmp_path, "run-1", "commit_message") is False
    assert artifact_receipt_present(tmp_path, "run-1", "development_result") is True


def test_delete_artifact_receipt_is_idempotent(tmp_path: Path) -> None:
    delete_artifact_receipt(tmp_path, "run-1", "commit_message")


def test_clear_run_receipts_scoped_to_run(tmp_path: Path) -> None:
    write_artifact_receipt(tmp_path, "run-1", "commit_message")
    write_artifact_receipt(tmp_path, "run-2", "commit_message")
    clear_run_receipts(tmp_path, "run-1")
    assert artifact_receipt_present(tmp_path, "run-2", "commit_message") is True


def test_receipt_written_to_db_not_files(tmp_path: Path) -> None:
    write_artifact_receipt(tmp_path, "run-1", "plan", receipt_secret="s3cret")
    assert not (tmp_path / ".agent" / "receipts").exists()
    assert artifact_receipt_present(tmp_path, "run-1", "plan", receipt_secret="s3cret")
    assert not artifact_receipt_present(tmp_path, "run-1", "plan", receipt_secret="wrong")


def test_legacy_file_receipt_still_honored(tmp_path: Path) -> None:
    legacy = tmp_path / ".agent" / "receipts" / "run-1" / "plan.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"run_id": "run-1", "artifact_type": "plan"}))
    assert artifact_receipt_present(tmp_path, "run-1", "plan")


def test_legacy_file_receipt_with_secret_honored(tmp_path: Path) -> None:
    legacy = tmp_path / ".agent" / "receipts" / "run-1" / "plan.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "artifact_type": "plan",
                "hmac": _receipt_hmac("s3cret", "run-1", "plan"),
            }
        )
    )
    assert artifact_receipt_present(tmp_path, "run-1", "plan", receipt_secret="s3cret")


def test_dual_target_delete_removes_legacy_file(tmp_path: Path) -> None:
    legacy = tmp_path / ".agent" / "receipts" / "run-1" / "plan.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"run_id": "run-1", "artifact_type": "plan"}))
    write_artifact_receipt(tmp_path, "run-1", "plan")
    delete_artifact_receipt(tmp_path, "run-1", "plan")
    assert not legacy.exists()
    assert artifact_receipt_present(tmp_path, "run-1", "plan") is False


def test_write_receipt_uses_fallback_when_db_unavailable(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:

    def _raise_sqlite_error(*_args: object, **_kwargs: object) -> object:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(receipts_module, "_open_db", _raise_sqlite_error)

    write_artifact_receipt(tmp_path, "run-1", "commit_message")
    assert (tmp_path / ".agent" / "receipts" / "run-1" / "commit_message.json").exists()


def test_read_receipt_falls_back_to_legacy_when_db_unavailable(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    legacy = tmp_path / ".agent" / "receipts" / "run-1" / "commit_message.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"run_id": "run-1", "artifact_type": "commit_message"}))

    def _raise_sqlite_error(*_args: object, **_kwargs: object) -> object:
        raise sqlite3.DatabaseError("unsupported")

    monkeypatch.setattr(receipts_module, "_open_db", _raise_sqlite_error)

    assert artifact_receipt_present(tmp_path, "run-1", "commit_message") is True


def test_delete_receipt_silent_when_db_unavailable(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    legacy = tmp_path / ".agent" / "receipts" / "run-1" / "commit_message.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"run_id": "run-1", "artifact_type": "commit_message"}))

    def _raise_sqlite_error(*_args: object, **_kwargs: object) -> object:
        raise sqlite3.DatabaseError("unsupported")

    monkeypatch.setattr(receipts_module, "_open_db", _raise_sqlite_error)

    delete_artifact_receipt(tmp_path, "run-1", "commit_message")
    assert not legacy.exists()


def test_write_receipt_falls_back_to_legacy_when_db_write_fails(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:

    class _FakeFailingDB:
        def upsert_receipt(self, *args: object, **kwargs: object) -> None:
            raise sqlite3.OperationalError("database is locked")

        def close(self) -> None:
            return None

    def _open_failing_db(workspace_root: Path) -> object:
        return _FakeFailingDB()

    monkeypatch.setattr(receipts_module, "_open_db", _open_failing_db)

    write_artifact_receipt(tmp_path, "run-1", "commit_message")

    legacy_path = tmp_path / ".agent" / "receipts" / "run-1" / "commit_message.json"
    assert legacy_path.exists()
    payload = json.loads(legacy_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-1"
    assert payload["artifact_type"] == "commit_message"


def test_legacy_fallback_stores_normalization_audit(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    def _raise_locked_db(_workspace_root: Path) -> object:
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(receipts_module, "_open_db", _raise_locked_db)
    audit: dict[str, object] = {"confidence": "high"}
    write_artifact_receipt(tmp_path, "run-1", "commit_message", normalization_audit=audit)
    legacy_path = tmp_path / ".agent" / "receipts" / "run-1" / "commit_message.json"
    assert json.loads(legacy_path.read_text(encoding="utf-8"))["normalization_audit"] == audit


def test_write_receipt_falls_back_to_legacy_when_db_upsert_raises_oserror(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:

    class _FakeFailingDB:
        def upsert_receipt(self, *args: object, **kwargs: object) -> None:
            raise OSError("disk I/O error")

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        receipts_module,
        "_open_db",
        lambda _workspace_root: _FakeFailingDB(),
    )

    write_artifact_receipt(tmp_path, "run-oserror", "commit_message")

    legacy_path = tmp_path / ".agent" / "receipts" / "run-oserror" / "commit_message.json"
    assert legacy_path.exists()

    def _raise_on_read(*_args: object, **_kwargs: object) -> object:
        raise OSError("database unavailable")

    monkeypatch.setattr(receipts_module, "_open_db", _raise_on_read)
    assert artifact_receipt_present(
        tmp_path,
        "run-oserror",
        "commit_message",
    )


def test_write_receipt_uses_db_without_redundant_legacy_write(
    tmp_path: Path,
) -> None:
    write_artifact_receipt(tmp_path, "run-1", "commit_message")
    legacy_path = tmp_path / ".agent" / "receipts" / "run-1" / "commit_message.json"
    assert not legacy_path.exists()
    assert artifact_receipt_present(tmp_path, "run-1", "commit_message") is True


def test_write_receipt_legacy_file_with_hmac_round_trips(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:

    def _open_failing_db(*_args: object, **_kwargs: object) -> object:
        raise sqlite3.DatabaseError("locked")

    monkeypatch.setattr(receipts_module, "_open_db", _open_failing_db)

    write_artifact_receipt(tmp_path, "run-1", "commit_message", receipt_secret="s3cret")

    legacy_path = tmp_path / ".agent" / "receipts" / "run-1" / "commit_message.json"
    payload = json.loads(legacy_path.read_text(encoding="utf-8"))
    assert payload["hmac"] == _receipt_hmac("s3cret", "run-1", "commit_message")
    assert (
        artifact_receipt_present(tmp_path, "run-1", "commit_message", receipt_secret="s3cret")
        is True
    )
    assert (
        artifact_receipt_present(tmp_path, "run-1", "commit_message", receipt_secret="wrong")
        is False
    )


class _AlwaysFailingBackend:
    def mkdir(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("disk full (mock)")

    def write_text(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("disk full (mock)")

    def read_text(self, *args: object, **kwargs: object) -> str:
        del args, kwargs
        raise OSError("disk full (mock)")

    def exists(self, *args: object, **kwargs: object) -> bool:
        del args, kwargs
        return True

    def unlink(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def replace(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("disk full (mock)")

    def glob(self, *args: object, **kwargs: object) -> list[Path]:
        del args, kwargs
        return []


def test_write_artifact_receipt_raises_when_db_open_and_legacy_fallback_both_fail(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:

    def _raise_sqlite(*_args: object, **_kwargs: object) -> object:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(receipts_module, "_open_db", _raise_sqlite)

    with pytest.raises(ReceiptPersistenceError) as excinfo:
        write_artifact_receipt(
            tmp_path,
            "run-fail-closed",
            "commit_message",
            backend=_AlwaysFailingBackend(),
        )

    assert "run-fail-closed" in str(excinfo.value)
    assert "commit_message" in str(excinfo.value)


def test_write_artifact_receipt_raises_when_db_upsert_and_legacy_both_fail(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:

    class _FakeFailingDB:
        def upsert_receipt(self, *args: object, **kwargs: object) -> None:
            raise sqlite3.OperationalError("database is locked")

        def close(self) -> None:
            return None

    monkeypatch.setattr(receipts_module, "_open_db", lambda _: _FakeFailingDB())

    with pytest.raises(ReceiptPersistenceError):
        write_artifact_receipt(
            tmp_path,
            "run-upsert-and-legacy-fail",
            "development_result",
            backend=_AlwaysFailingBackend(),
        )


class _RecordingReceiptBackend:
    def __init__(self, files: dict[Path, str]) -> None:
        self.files = files
        self.mkdir_calls: list[Path] = []
        self.write_calls: list[tuple[Path, str]] = []

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del parents, exist_ok
        self.mkdir_calls.append(path)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del encoding
        return self.files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        del encoding
        self.write_calls.append((path, content))
        self.files[path] = content


def test_legacy_receipt_fallback_regression_identical_replay_skips_parent_mutation(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:

    def _raise_sqlite(*_args: object, **_kwargs: object) -> object:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(receipts_module, "_open_db", _raise_sqlite)
    path = tmp_path / ".agent" / "receipts" / "run-1" / "plan.json"
    payload = json.dumps({"run_id": "run-1", "artifact_type": "plan"})
    backend = _RecordingReceiptBackend({path: payload})

    result = write_artifact_receipt(tmp_path, "run-1", "plan", backend=backend)

    assert result == path
    assert backend.mkdir_calls == []
    assert backend.write_calls == []
    assert backend.files[path] == payload


def test_write_artifact_receipt_does_not_raise_when_legacy_fallback_writes(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:

    def _raise_sqlite(*_args: object, **_kwargs: object) -> object:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(receipts_module, "_open_db", _raise_sqlite)

    write_artifact_receipt(tmp_path, "run-legacy-only", "commit_message")


def test_clear_run_receipts_does_not_raise_when_db_clear_fails(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:

    legacy = tmp_path / ".agent" / "receipts" / "run-1" / "commit_message.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"run_id": "run-1", "artifact_type": "commit_message"}))

    class _FakeFailingClearDB:
        def clear_run_receipts(self, run_id: str) -> None:
            del run_id
            raise sqlite3.OperationalError("database is locked")

        def close(self) -> None:
            return None

    monkeypatch.setattr(receipts_module, "_open_db", lambda _: _FakeFailingClearDB())

    clear_run_receipts(tmp_path, "run-1")

    assert not legacy.exists()


def test_clear_run_receipts_does_not_raise_when_db_open_fails(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    legacy_a = tmp_path / ".agent" / "receipts" / "run-1" / "a.json"
    legacy_b = tmp_path / ".agent" / "receipts" / "run-1" / "b.json"
    legacy_a.parent.mkdir(parents=True)
    legacy_a.write_text(json.dumps({"run_id": "run-1"}))
    legacy_b.write_text(json.dumps({"run_id": "run-1"}))

    def _raise_sqlite(*_args: object, **_kwargs: object) -> object:
        raise sqlite3.DatabaseError("unsupported")

    monkeypatch.setattr(receipts_module, "_open_db", _raise_sqlite)

    clear_run_receipts(tmp_path, "run-1")

    assert not legacy_a.exists()
    assert not legacy_b.exists()
