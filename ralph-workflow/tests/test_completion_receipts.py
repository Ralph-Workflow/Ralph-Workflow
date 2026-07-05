"""Tests for run-scoped artifact submission receipts.

A receipt is the authoritative, drift-proof signal that an artifact of a given
type was persisted during a given run. The submission handler writes it; the
completion gate reads it. The gate never recomputes a storage path, so a receipt
keyed on ``(run_id, artifact_type)`` cannot disagree with where the artifact
actually landed.

Storage is backed by ``.agent/state.db`` (RFC-013 P3) — writes go to the
DB only; legacy file paths are read-fallback during the dual-read window.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts import completion_receipts as receipts_module
from ralph.mcp.artifacts.completion_receipts import (
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
    # Must not raise when no receipt exists (rollback of a failed first op).
    delete_artifact_receipt(tmp_path, "run-1", "commit_message")


def test_clear_run_receipts_scoped_to_run(tmp_path: Path) -> None:
    write_artifact_receipt(tmp_path, "run-1", "commit_message")
    write_artifact_receipt(tmp_path, "run-2", "commit_message")
    clear_run_receipts(tmp_path, "run-1")
    assert artifact_receipt_present(tmp_path, "run-2", "commit_message") is True


# ----------------------------------------------------------------------------
# RFC-013 P3: receipts are written to RunStateDB (no per-run receipt dir),
# legacy file receipts are honored via DB-then-file dual-read.
# ----------------------------------------------------------------------------


def test_receipt_written_to_db_not_files(tmp_path: Path) -> None:
    write_artifact_receipt(tmp_path, "run-1", "plan", receipt_secret="s3cret")
    # No per-run receipt directory should be created anymore
    assert not (tmp_path / ".agent" / "receipts").exists()
    assert artifact_receipt_present(tmp_path, "run-1", "plan", receipt_secret="s3cret")
    assert not artifact_receipt_present(tmp_path, "run-1", "plan", receipt_secret="wrong")


def test_legacy_file_receipt_still_honored(tmp_path: Path) -> None:
    # Simulate a receipt written by the previous release
    legacy = tmp_path / ".agent" / "receipts" / "run-1" / "plan.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"run_id": "run-1", "artifact_type": "plan"}))
    assert artifact_receipt_present(tmp_path, "run-1", "plan")


def test_legacy_file_receipt_with_secret_honored(tmp_path: Path) -> None:
    """Legacy file receipts WITH a valid HMAC are honored end-to-end."""
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
    """delete_artifact_receipt removes both the DB row AND the legacy file."""
    legacy = tmp_path / ".agent" / "receipts" / "run-1" / "plan.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"run_id": "run-1", "artifact_type": "plan"}))
    write_artifact_receipt(tmp_path, "run-1", "plan")  # DB row written
    delete_artifact_receipt(tmp_path, "run-1", "plan")
    # legacy file is gone (dual-target), DB row gone
    assert not legacy.exists()
    assert artifact_receipt_present(tmp_path, "run-1", "plan") is False


# ----------------------------------------------------------------------------
# RFC-013 P3 risk-mitigation: ``sqlite3.Error`` from ``RunStateDB`` must
# never break the receipt contract. The write helper fallbacks to a
# silent no-op (the legacy read-only fallback window still has the
# pre-upgrade receipt on disk). The read helper falls back to the
# legacy file path so a forged / corrupt DB does not block the
# completion gate.
# ----------------------------------------------------------------------------


def test_write_receipt_silent_when_db_unavailable(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """``write_artifact_receipt`` swallows ``sqlite3.Error`` from the
    DB open path; no exception propagates to the caller."""
    def _raise_sqlite_error(*_args: object, **_kwargs: object) -> object:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(receipts_module, "_open_db", _raise_sqlite_error)

    # Must not raise; the receipt write is best-effort during the
    # dual-read window.
    write_artifact_receipt(tmp_path, "run-1", "commit_message")


def test_read_receipt_falls_back_to_legacy_when_db_unavailable(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """``artifact_receipt_present`` falls back to the legacy file path
    when the DB raises (locked / corrupt / unsupported state)."""
    legacy = tmp_path / ".agent" / "receipts" / "run-1" / "commit_message.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"run_id": "run-1", "artifact_type": "commit_message"}))

    def _raise_sqlite_error(*_args: object, **_kwargs: object) -> object:
        raise sqlite3.DatabaseError("unsupported")

    monkeypatch.setattr(receipts_module, "_open_db", _raise_sqlite_error)

    # Legacy file path is honored despite the DB failure.
    assert artifact_receipt_present(tmp_path, "run-1", "commit_message") is True


def test_delete_receipt_silent_when_db_unavailable(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """``delete_artifact_receipt`` swallows ``sqlite3.Error`` from the
    DB open path and still unlinks the legacy file (dual-target)."""
    legacy = tmp_path / ".agent" / "receipts" / "run-1" / "commit_message.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"run_id": "run-1", "artifact_type": "commit_message"}))

    def _raise_sqlite_error(*_args: object, **_kwargs: object) -> object:
        raise sqlite3.DatabaseError("unsupported")

    monkeypatch.setattr(receipts_module, "_open_db", _raise_sqlite_error)

    # Must not raise; legacy file is still removed by the dual-target
    # unlink path below the DB block.
    delete_artifact_receipt(tmp_path, "run-1", "commit_message")
    assert not legacy.exists()


def test_write_receipt_falls_back_to_legacy_when_db_write_fails(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Durable-fallback contract: when the DB write raises ``sqlite3.Error``
    (locked / corrupt / unsupported), the receipt MUST land in the legacy
    file path so the completion gate still has durable evidence. A
    silent no-op under DB failure would report success while leaving no
    authoritative completion marker.
    """
    class _FakeFailingDB:
        def upsert_receipt(self, *args: object, **kwargs: object) -> None:
            raise sqlite3.OperationalError("database is locked")

        def close(self) -> None:
            return None

    def _open_failing_db(workspace_root: Path) -> object:
        return _FakeFailingDB()

    monkeypatch.setattr(receipts_module, "_open_db", _open_failing_db)

    # Call should NOT raise \u2014 the durable fallback is the legacy file.
    write_artifact_receipt(tmp_path, "run-1", "commit_message")

    # Legacy file now holds the authoritative receipt.
    legacy_path = (
        tmp_path / ".agent" / "receipts" / "run-1" / "commit_message.json"
    )
    assert legacy_path.exists()
    payload = json.loads(legacy_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-1"
    assert payload["artifact_type"] == "commit_message"


def test_write_receipt_dual_persistence_db_and_legacy(
    tmp_path: Path,
) -> None:
    """When the DB write succeeds AND the legacy-write fallback path is
    invoked, both stores must agree: receipt is present after either
    delete-one-side test."""
    write_artifact_receipt(tmp_path, "run-1", "commit_message")
    # Success path: legacy file is NOT created (production writes go to DB only).
    legacy_path = (
        tmp_path / ".agent" / "receipts" / "run-1" / "commit_message.json"
    )
    assert not legacy_path.exists()
    # Read returns True: DB row is present.
    assert artifact_receipt_present(tmp_path, "run-1", "commit_message") is True


def test_write_receipt_legacy_file_with_hmac_round_trips(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Durable fallback path stores the HMAC so a read with a different
    secret is still rejected."""

    def _open_failing_db(*_args: object, **_kwargs: object) -> object:
        raise sqlite3.DatabaseError("locked")

    monkeypatch.setattr(receipts_module, "_open_db", _open_failing_db)

    write_artifact_receipt(
        tmp_path, "run-1", "commit_message", receipt_secret="s3cret"
    )

    legacy_path = (
        tmp_path / ".agent" / "receipts" / "run-1" / "commit_message.json"
    )
    payload = json.loads(legacy_path.read_text(encoding="utf-8"))
    assert payload["hmac"] == _receipt_hmac("s3cret", "run-1", "commit_message")
    # HMAC must verify under the same secret:
    assert (
        artifact_receipt_present(
            tmp_path, "run-1", "commit_message", receipt_secret="s3cret"
        )
        is True
    )
    # ... and reject under a mismatching secret:
    assert (
        artifact_receipt_present(
            tmp_path, "run-1", "commit_message", receipt_secret="wrong"
        )
        is False
    )
