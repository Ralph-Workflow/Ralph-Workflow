from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.artifacts import canonical_submit as canonical_submit_module
from ralph.mcp.artifacts.canonical_submit import submit_artifact_canonical
from ralph.mcp.artifacts.completion_receipts import (
    ReceiptPersistenceError,
    read_artifact_submission_snapshot,
)
from ralph.mcp.tools.artifact import ArtifactHandlerDeps

if TYPE_CHECKING:
    from ralph.mcp.artifacts.file_backend import FileBackend

PLAN = """---
type: plan
---

## Approved deletions

- `old/a.py`
"""
SECRET = "plan-snapshot-receipt-secret"


def submit_plan(tmp_path: Path, content: str) -> None:
    submit_artifact_canonical(
        tmp_path,
        "plan",
        {},
        markdown=content,
        run_id="run-current",
        deps=ArtifactHandlerDeps(receipt_secret=SECRET),
    )


def test_plan_submission_snapshot_receipt_binds_exact_submitted_bytes_and_digest(
    tmp_path: Path,
) -> None:
    submit_plan(tmp_path, PLAN)

    snapshot = read_artifact_submission_snapshot(
        tmp_path, "run-current", "plan", receipt_secret=SECRET
    )

    assert snapshot is not None
    relative_path, digest = snapshot
    snapshot_path = tmp_path / relative_path
    assert snapshot_path.read_bytes() == PLAN.encode("utf-8")
    assert digest == hashlib.sha256(PLAN.encode("utf-8")).hexdigest()
    assert (
        read_artifact_submission_snapshot(tmp_path, "run-other", "plan", receipt_secret=SECRET)
        is None
    )


def test_failed_plan_submission_restores_snapshot_and_receipt_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submit_plan(tmp_path, PLAN)
    before = read_artifact_submission_snapshot(
        tmp_path, "run-current", "plan", receipt_secret=SECRET
    )
    assert before is not None
    before_path, _before_digest = before
    before_bytes = (tmp_path / before_path).read_bytes()

    def fail_receipt(*_args: object, **_kwargs: object) -> None:
        raise ReceiptPersistenceError("receipt unavailable")

    monkeypatch.setattr(canonical_submit_module, "write_artifact_receipt", fail_receipt)
    updated = PLAN.replace("old/a.py", "old/b.py")

    with pytest.raises(ReceiptPersistenceError, match="receipt unavailable"):
        submit_artifact_canonical(
            tmp_path,
            "plan",
            {},
            markdown=updated,
            run_id="run-current",
            deps=ArtifactHandlerDeps(receipt_secret=SECRET),
        )

    after = read_artifact_submission_snapshot(
        tmp_path, "run-current", "plan", receipt_secret=SECRET
    )
    assert after == before
    assert (tmp_path / before_path).read_bytes() == before_bytes


def test_failed_plan_replacement_after_receipt_write_restores_previous_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submit_plan(tmp_path, PLAN)
    before = read_artifact_submission_snapshot(
        tmp_path, "run-current", "plan", receipt_secret=SECRET
    )
    assert before is not None
    before_path, _before_digest = before
    before_bytes = (tmp_path / before_path).read_bytes()
    with sqlite3.connect(tmp_path / ".agent/state.db") as connection:
        before_receipt = connection.execute(
            "SELECT hmac, submission_snapshot_path, submission_snapshot_digest, "
            "submission_snapshot_hmac FROM receipts WHERE run_id = ? AND artifact_type = ?",
            ("run-current", "plan"),
        ).fetchone()
    original = canonical_submit_module.write_artifact_receipt

    def write_then_fail(
        workspace_root: Path,
        run_id: str,
        artifact_type: str,
        *,
        backend: FileBackend,
        receipt_secret: str | None = None,
        normalization_audit: dict[str, object] | None = None,
        submission_snapshot_path: str | None = None,
        submission_snapshot_digest: str | None = None,
    ) -> Path:
        original(
            workspace_root,
            run_id,
            artifact_type,
            backend=backend,
            receipt_secret=receipt_secret,
            normalization_audit=normalization_audit,
            submission_snapshot_path=submission_snapshot_path,
            submission_snapshot_digest=submission_snapshot_digest,
        )
        raise ReceiptPersistenceError("failure after receipt write")

    monkeypatch.setattr(canonical_submit_module, "write_artifact_receipt", write_then_fail)

    with pytest.raises(ReceiptPersistenceError, match="failure after receipt write"):
        submit_plan(tmp_path, PLAN.replace("old/a.py", "old/b.py"))

    after = read_artifact_submission_snapshot(
        tmp_path, "run-current", "plan", receipt_secret=SECRET
    )
    assert after == before
    assert (tmp_path / before_path).read_bytes() == before_bytes
    with sqlite3.connect(tmp_path / ".agent/state.db") as connection:
        after_receipt = connection.execute(
            "SELECT hmac, submission_snapshot_path, submission_snapshot_digest, "
            "submission_snapshot_hmac FROM receipts WHERE run_id = ? AND artifact_type = ?",
            ("run-current", "plan"),
        ).fetchone()
    assert after_receipt == before_receipt


@pytest.mark.parametrize("run_id,artifact_type", [("../escape", "plan"), ("run", "../plan")])
def test_snapshot_path_components_are_rejected_before_write(
    tmp_path: Path,
    run_id: str,
    artifact_type: str,
) -> None:
    with pytest.raises(ValueError, match="invalid .*submission snapshot"):
        submit_artifact_canonical(
            tmp_path,
            artifact_type,
            {},
            markdown=PLAN,
            run_id=run_id,
            deps=ArtifactHandlerDeps(receipt_secret=SECRET),
        )
    assert not (tmp_path.parent / "escape").exists()
