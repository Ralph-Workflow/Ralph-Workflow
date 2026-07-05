"""Run-scoped artifact submission receipts — the single source of truth for
"was this artifact submitted in this run?".

A receipt decouples *completion detection* from *artifact storage*. The
submission handler writes a receipt the moment it has durably persisted an
artifact; the completion gate reads receipts to decide whether the required
artifact is present. The gate never recomputes a storage path, so a receipt
keyed on ``(run_id, artifact_type)`` — both stable identities, never paths —
cannot drift away from where the artifact actually landed (``.agent/tmp`` vs
``.agent/artifacts``, a per-worker namespace, or any future layout change).

Storage (RFC-013 P3): receipts are stored in a single WAL-mode SQLite
database at ``<workspace>/.agent/state.db`` via ``RunStateDB`` (one row
per ``(run_id, artifact_type)``). This eliminates one-file-per-event
state churn under ``.agent/receipts/<run_id>/`` (a measurable share of
macOS fseventsd activity under long multi-instance runs). The legacy
file path is preserved as a read-fallback during the dual-read rollout
window so an in-flight run that was upgraded mid-run still passes its
completion gate. Production writes go to the DB only; the file path is
read-only fallback.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import sqlite3
from pathlib import Path
from typing import cast

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.state_db import MISSING, RunStateDB, _Missing

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


def _open_db(workspace_root: Path) -> RunStateDB:
    return RunStateDB(Path(workspace_root))


def _legacy_file_receipt_present(
    workspace_root: Path,
    run_id: str,
    artifact_type: str,
    *,
    backend: FileBackend,
    receipt_secret: str | None,
) -> bool:
    """Read the legacy ``.agent/receipts/<run_id>/<type>.json`` file path."""
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

    Storage (RFC-013 P3): the canonical store is the per-workspace
    ``.agent/state.db``. Production writes go to the DB ONLY when the
    DB write succeeds; the legacy ``.agent/receipts/<run_id>/<artifact_type>.json``
    file path is then read-only fallback during the dual-read rollout
    window so receipts left behind by the pre-upgrade release are still
    honored.

    Durable-fallback: when ``RunStateDB`` raises ``sqlite3.Error``
    (locked / corrupt / unsupported WAL) on either open or upsert,
    this function falls back to writing the legacy file path so the
    completion gate always has durable evidence. Atomic-rollback for
    tests and callers using explicit ``backend`` kwargs still works
    because ``backend`` continues to control where the legacy bytes
    land (see ``FailingBackend`` pattern). The HMAC is included in
    both stores when ``receipt_secret`` is provided.
    """
    hmac_hex: str | None
    if receipt_secret is not None:
        hmac_hex = _receipt_hmac(receipt_secret, run_id, artifact_type)
    else:
        hmac_hex = None

    db_written = False
    db: RunStateDB | None = None
    try:
        db = _open_db(workspace_root)
    except (OSError, RuntimeError, sqlite3.Error):
        db = None
    if db is not None:
        try:
            db.upsert_receipt(run_id, artifact_type, hmac_hex)
            db_written = True
        except sqlite3.Error:
            pass  # Will fall through to legacy-file durable fallback below.
        finally:
            with contextlib.suppress(OSError, RuntimeError, sqlite3.Error):
                db.close()

    if db_written:
        return

    _write_legacy_receipt_fallback(
        workspace_root,
        run_id,
        artifact_type,
        hmac_hex=hmac_hex,
        backend=backend,
    )


def _write_legacy_receipt_fallback(
    workspace_root: Path,
    run_id: str,
    artifact_type: str,
    *,
    hmac_hex: str | None,
    backend: FileBackend,
) -> None:
    """Write the legacy ``.agent/receipts/<run_id>/<artifact_type>.json`` fallback.

    Used by ``write_artifact_receipt`` only when the RunStateDB write
    fails (sqlite3.Error on open or upsert). The HMAC is included in
    the payload when one was provided so a subsequent read with the
    same secret verifies and a mismatching secret rejects.
    """
    path = _receipt_path(workspace_root, run_id, artifact_type)
    payload: dict[str, object] = {"run_id": run_id, "artifact_type": artifact_type}
    if hmac_hex is not None:
        payload["hmac"] = hmac_hex
    try:
        backend.mkdir(_receipt_dir(workspace_root, run_id), parents=True, exist_ok=True)
        backend.write_text(path, json.dumps(payload), encoding="utf-8")
    except OSError:
        return  # Both DB and legacy paths failed - nothing durable to write.


def artifact_receipt_present(
    workspace_root: Path,
    run_id: str,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    receipt_secret: str | None = None,
) -> bool:
    """Return True when a valid receipt for ``(run_id, artifact_type)`` exists.

    Reads the per-workspace ``.agent/state.db`` first (RFC-013 P3). When
    the DB has no row, falls back to the legacy file path
    ``.agent/receipts/<run_id>/<artifact_type>.json`` so receipts left
    behind by the pre-upgrade release are still honored during the
    dual-read window.

    When ``receipt_secret`` is provided the stored HMAC is verified
    against ``(run_id, artifact_type)``; a receipt that exists but
    fails HMAC verification returns ``False``. This pins the receipt
    to the broker-owned secret so a model with workspace write
    capabilities cannot forge a valid receipt.
    """
    try:
        db = _open_db(workspace_root)
    except (OSError, RuntimeError, sqlite3.Error):
        # DB unavailable — fall back to the legacy file path.
        return _legacy_file_receipt_present(
            workspace_root,
            run_id,
            artifact_type,
            backend=backend,
            receipt_secret=receipt_secret,
        )
    stored: str | None | _Missing
    try:
        stored = db.get_receipt_hmac(run_id, artifact_type)
    except (OSError, RuntimeError, sqlite3.Error):
        # DB read failed; close and fall back to legacy file path.
        with contextlib.suppress(OSError, RuntimeError, sqlite3.Error):
            db.close()
        return _legacy_file_receipt_present(
            workspace_root,
            run_id,
            artifact_type,
            backend=backend,
            receipt_secret=receipt_secret,
        )
    with contextlib.suppress(OSError, RuntimeError, sqlite3.Error):
        db.close()

    if stored is not MISSING:
        if receipt_secret is None:
            return True
        if not isinstance(stored, str):
            return False
        expected = _receipt_hmac(receipt_secret, run_id, artifact_type)
        return hmac.compare_digest(stored, expected)

    # DB has no row — read the legacy file path so an in-flight run that
    # was upgraded mid-run still passes its completion gate.
    return _legacy_file_receipt_present(
        workspace_root,
        run_id,
        artifact_type,
        backend=backend,
        receipt_secret=receipt_secret,
    )


def delete_artifact_receipt(
    workspace_root: Path,
    run_id: str,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Remove one receipt (no-op when absent) — the undo for ``write_artifact_receipt``.

    Deletes both the DB row and the legacy file path (dual-target)
    so a stale file from the pre-upgrade release cannot leave a
    receipt in place after the DB row is gone.
    """
    try:
        db = _open_db(workspace_root)
    except (OSError, RuntimeError, sqlite3.Error):
        db = None
    if db is not None:
        try:
            db.delete_receipt(run_id, artifact_type)
        finally:
            with contextlib.suppress(OSError, RuntimeError, sqlite3.Error):
                db.close()
    backend.unlink(_receipt_path(workspace_root, run_id, artifact_type), missing_ok=True)


def clear_run_receipts(
    workspace_root: Path,
    run_id: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Remove every receipt for ``run_id`` (no-op when none exist).

    Called at the start of each (re)invocation so a resumed session
    with a reused ``run_id`` never inherits a stale "already submitted"
    signal. Clears both the DB rows and the legacy file paths.
    Best-effort: a missing or read-only ``.agent/state.db`` does not
    block the call (the legacy file cleanup still proceeds).
    """
    try:
        db = _open_db(workspace_root)
    except (OSError, RuntimeError, sqlite3.Error):
        db = None
    if db is not None:
        try:
            db.clear_run_receipts(run_id)
        finally:
            with contextlib.suppress(OSError, RuntimeError, sqlite3.Error):
                db.close()
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
