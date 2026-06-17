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

import hashlib
import hmac
import json
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend

if TYPE_CHECKING:
    from pathlib import Path

#: Directory (workspace-relative) holding every receipt for a single run.
RECEIPT_DIR_RELPATH_FMT = ".agent/receipts/{run_id}"


def _receipt_dir(workspace_root: Path, run_id: str) -> Path:
    return workspace_root / RECEIPT_DIR_RELPATH_FMT.format(run_id=run_id)


def _receipt_path(workspace_root: Path, run_id: str, artifact_type: str) -> Path:
    return _receipt_dir(workspace_root, run_id) / f"{artifact_type}.json"


def _receipt_hmac(secret: str, run_id: str, artifact_type: str) -> str:
    """Compute the HMAC-SHA256 of ``run_id`` and ``artifact_type`` with ``secret``.

    The HMAC binds the receipt to the broker-owned ``secret`` so a model
    that can write under ``.agent/`` (workspace write capabilities) cannot
    forge a valid receipt without the secret. The secret is never exposed
    via the agent's environment (notably not via ``MCP_RUN_ID_ENV`` or
    any other broker-exposed variable).
    """
    msg = f"{run_id}\n{artifact_type}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def write_artifact_receipt(
    workspace_root: Path,
    run_id: str,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    receipt_secret: str | None = None,
) -> None:
    """Record that ``artifact_type`` was durably persisted during ``run_id``.

    Must be called only after the artifact itself is committed to storage so the
    receipt and the artifact appear together (or, on rollback, not at all).

    When ``receipt_secret`` is provided the receipt includes a ``hmac``
    field that binds it to the broker-owned secret. A model that can
    write under ``.agent/`` cannot forge a receipt with a valid HMAC
    because the secret is never exposed to the agent.
    """
    path = _receipt_path(workspace_root, run_id, artifact_type)
    backend.mkdir(path.parent, parents=True, exist_ok=True)
    receipt: dict[str, str] = {"run_id": run_id, "artifact_type": artifact_type}
    if receipt_secret is not None:
        receipt["hmac"] = _receipt_hmac(receipt_secret, run_id, artifact_type)
    backend.write_text(path, json.dumps(receipt), encoding="utf-8")


def artifact_receipt_present(
    workspace_root: Path,
    run_id: str,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    receipt_secret: str | None = None,
) -> bool:
    """Return True when a valid receipt for ``(run_id, artifact_type)`` exists.

    When ``receipt_secret`` is provided the receipt's ``hmac`` field is
    verified against ``(run_id, artifact_type)``; a receipt that exists
    on disk but fails HMAC verification returns ``False``. This pins
    the receipt to the broker-owned secret so a model with workspace
    write capabilities cannot forge a valid receipt.
    """
    path = _receipt_path(workspace_root, run_id, artifact_type)
    if not backend.exists(path):
        return False
    if receipt_secret is None:
        return True
    try:
        raw = backend.read_text(path, encoding="utf-8")
        parsed = cast("object", json.loads(raw))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(parsed, dict):
        return False
    stored = cast("dict[str, object]", parsed).get("hmac")
    if not isinstance(stored, str):
        return False
    expected = _receipt_hmac(receipt_secret, run_id, artifact_type)
    return hmac.compare_digest(stored, expected)


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
