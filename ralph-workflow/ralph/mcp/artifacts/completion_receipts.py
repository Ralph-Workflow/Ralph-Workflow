"""Run-scoped artifact submission receipts — the single source of truth for
"was this artifact submitted in this run?".

A receipt decouples *completion detection* from *artifact storage*. The
submission handler writes a receipt the moment it has durably persisted an
artifact; the completion gate reads receipts to decide whether the required
artifact is present. The gate never recomputes a storage path, so a receipt
keyed on ``(run_id, artifact_type)`` — both stable identities, never paths —
cannot drift away from where the artifact actually landed (``.agent/tmp`` vs
``.agent/artifacts``, a per-worker namespace, or any future layout change).

Receipts live under ``.agent/receipts/<run_id>/<artifact_type>.json`` so a fresh
``run_id`` is always clean and ``clear_run_receipts`` can scrub exactly one run
on (re)start without touching siblings or parallel workers.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend

if TYPE_CHECKING:
    from pathlib import Path

#: Directory (workspace-relative) holding every receipt for a single run.
RECEIPT_DIR_RELPATH_FMT = ".agent/receipts/{run_id}"


def _receipt_dir(workspace_root: Path, run_id: str) -> Path:
    return workspace_root / RECEIPT_DIR_RELPATH_FMT.format(run_id=run_id)


def _receipt_path(workspace_root: Path, run_id: str, artifact_type: str) -> Path:
    return _receipt_dir(workspace_root, run_id) / f"{artifact_type}.json"


def write_artifact_receipt(
    workspace_root: Path,
    run_id: str,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Record that ``artifact_type`` was durably persisted during ``run_id``.

    Must be called only after the artifact itself is committed to storage so the
    receipt and the artifact appear together (or, on rollback, not at all).
    """
    path = _receipt_path(workspace_root, run_id, artifact_type)
    backend.mkdir(path.parent, parents=True, exist_ok=True)
    receipt: dict[str, str] = {"run_id": run_id, "artifact_type": artifact_type}
    backend.write_text(path, json.dumps(receipt), encoding="utf-8")


def artifact_receipt_present(
    workspace_root: Path,
    run_id: str,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> bool:
    """Return True when a receipt for ``(run_id, artifact_type)`` exists."""
    return backend.exists(_receipt_path(workspace_root, run_id, artifact_type))


def delete_artifact_receipt(
    workspace_root: Path,
    run_id: str,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Remove one receipt (no-op when absent) — the undo for ``write_artifact_receipt``."""
    backend.unlink(_receipt_path(workspace_root, run_id, artifact_type), missing_ok=True)


def clear_run_receipts(
    workspace_root: Path,
    run_id: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Remove every receipt for ``run_id`` (no-op when none exist).

    Called at the start of each (re)invocation so a resumed session with a reused
    ``run_id`` never inherits a stale "already submitted" signal.
    """
    receipt_dir = _receipt_dir(workspace_root, run_id)
    for path in backend.glob(receipt_dir, "*.json"):
        backend.unlink(path, missing_ok=True)


__all__ = [
    "RECEIPT_DIR_RELPATH_FMT",
    "artifact_receipt_present",
    "clear_run_receipts",
    "delete_artifact_receipt",
    "write_artifact_receipt",
]
