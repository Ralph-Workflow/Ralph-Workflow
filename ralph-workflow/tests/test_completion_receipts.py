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
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.completion_receipts import (
    _receipt_hmac,
    artifact_receipt_present,
    clear_run_receipts,
    delete_artifact_receipt,
    write_artifact_receipt,
)

if TYPE_CHECKING:
    from pathlib import Path


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
