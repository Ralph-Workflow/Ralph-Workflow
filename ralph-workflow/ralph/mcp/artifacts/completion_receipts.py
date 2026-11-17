from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import sqlite3
from pathlib import Path
from typing import TypeGuard, cast

from ralph.checked_accessors import as_str, optional_str_list
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.mcp.artifacts.state_db import DB_RELPATH, MISSING, RunStateDB, _Missing

RECEIPT_DIR_RELPATH_FMT = ".agent/receipts/{run_id}"


class ReceiptPersistenceError(RuntimeError):
    pass


type ArtifactSubmissionSnapshot = tuple[str, str]


def _all_strings(values: tuple[object, ...]) -> TypeGuard[tuple[str, ...]]:
    return all(isinstance(value, str) for value in values)


def _receipt_dir(workspace_root: Path, run_id: str) -> Path:
    return workspace_root / RECEIPT_DIR_RELPATH_FMT.format(run_id=run_id)


def _receipt_path(workspace_root: Path, run_id: str, artifact_type: str) -> Path:
    return _receipt_dir(workspace_root, run_id) / f"{artifact_type}.json"


def _receipt_hmac(secret: str, run_id: str, artifact_type: str) -> str:
    msg = f"{run_id}\n{artifact_type}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def _snapshot_hmac(secret: str, run_id: str, artifact_type: str, path: str, digest: str) -> str:
    msg = f"{run_id}\n{artifact_type}\n{path}\n{digest}".encode()
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
    path = _receipt_path(workspace_root, run_id, artifact_type)
    if not backend.exists(path):
        return False
    if receipt_secret is None:
        return True
    try:
        raw = backend.read_text(path, encoding="utf-8")
        parsed: object = json.loads(raw)
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
    normalization_audit: dict[str, object] | None = None,
    submission_snapshot_path: str | None = None,
    submission_snapshot_digest: str | None = None,
) -> Path:
    hmac_hex: str | None
    snapshot_hmac: str | None = None
    if receipt_secret is not None:
        hmac_hex = _receipt_hmac(receipt_secret, run_id, artifact_type)
    else:
        hmac_hex = None
    if (
        receipt_secret is not None
        and submission_snapshot_path is not None
        and submission_snapshot_digest is not None
    ):
        snapshot_hmac = _snapshot_hmac(
            receipt_secret,
            run_id,
            artifact_type,
            submission_snapshot_path,
            submission_snapshot_digest,
        )

    audit_json = json.dumps(normalization_audit, sort_keys=True) if normalization_audit else None
    db_written = False
    db: RunStateDB | None = None
    try:
        db = _open_db(workspace_root)
    except (OSError, RuntimeError, sqlite3.Error):
        db = None
    if db is not None:
        try:
            if (
                submission_snapshot_path is None
                and submission_snapshot_digest is None
                and snapshot_hmac is None
            ):
                if normalization_audit is None:
                    db.upsert_receipt(run_id, artifact_type, hmac_hex)
                else:
                    db.upsert_receipt(run_id, artifact_type, hmac_hex, audit_json)
            else:
                db.upsert_receipt(
                    run_id,
                    artifact_type,
                    hmac_hex,
                    audit_json,
                    submission_snapshot_path,
                    submission_snapshot_digest,
                    snapshot_hmac,
                )
            db_written = True
        except (OSError, RuntimeError, sqlite3.Error):
            pass
        finally:
            with contextlib.suppress(OSError, RuntimeError, sqlite3.Error):
                db.close()

    if db_written:
        return workspace_root / DB_RELPATH

    legacy_path = _receipt_path(workspace_root, run_id, artifact_type)
    legacy_written = _write_legacy_receipt_fallback(
        workspace_root,
        run_id,
        artifact_type,
        hmac_hex=hmac_hex,
        normalization_audit=normalization_audit,
        submission_snapshot_path=submission_snapshot_path,
        submission_snapshot_digest=submission_snapshot_digest,
        submission_snapshot_hmac=snapshot_hmac,
        backend=backend,
    )
    if not legacy_written:
        raise ReceiptPersistenceError(
            f"Both DB and legacy paths failed to persist receipt for "
            f"run_id={run_id!r} artifact_type={artifact_type!r}"
        )
    return legacy_path


def _write_legacy_receipt_fallback(
    workspace_root: Path,
    run_id: str,
    artifact_type: str,
    *,
    hmac_hex: str | None,
    normalization_audit: dict[str, object] | None,
    submission_snapshot_path: str | None,
    submission_snapshot_digest: str | None,
    submission_snapshot_hmac: str | None,
    backend: FileBackend,
) -> bool:
    path = _receipt_path(workspace_root, run_id, artifact_type)
    payload: dict[str, object] = {"run_id": run_id, "artifact_type": artifact_type}
    if hmac_hex is not None:
        payload["hmac"] = hmac_hex
    if normalization_audit is not None:
        payload["normalization_audit"] = normalization_audit
    if submission_snapshot_path is not None:
        payload["submission_snapshot_path"] = submission_snapshot_path
    if submission_snapshot_digest is not None:
        payload["submission_snapshot_digest"] = submission_snapshot_digest
    if submission_snapshot_hmac is not None:
        payload["submission_snapshot_hmac"] = submission_snapshot_hmac
    try:
        write_text_if_changed(
            backend,
            path,
            json.dumps(payload),
            encoding="utf-8",
            prepare_write=lambda: backend.mkdir(
                _receipt_dir(workspace_root, run_id), parents=True, exist_ok=True
            ),
        )
    except OSError:
        return False
    return True


def _snapshot_from_payload(
    payload: object,
    run_id: str,
    artifact_type: str,
    receipt_secret: str,
) -> ArtifactSubmissionSnapshot | None:
    if not isinstance(payload, dict):
        return None
    values = cast("dict[str, object]", payload)
    path = values.get("submission_snapshot_path")
    digest = values.get("submission_snapshot_digest")
    stored_hmac = values.get("submission_snapshot_hmac")
    if not isinstance(path, str) or not isinstance(digest, str) or not isinstance(stored_hmac, str):
        return None
    expected = _snapshot_hmac(receipt_secret, run_id, artifact_type, path, digest)
    if not hmac.compare_digest(stored_hmac, expected):
        return None
    return path, digest


def read_artifact_submission_snapshot(
    workspace_root: Path,
    run_id: str,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    receipt_secret: str | None = None,
) -> ArtifactSubmissionSnapshot | None:
    if receipt_secret is None or not artifact_receipt_present(
        workspace_root,
        run_id,
        artifact_type,
        backend=backend,
        receipt_secret=receipt_secret,
    ):
        return None
    try:
        db = _open_db(workspace_root)
    except (OSError, RuntimeError, sqlite3.Error):
        db = None
    if db is not None:
        try:
            stored = db.get_receipt_submission_snapshot(run_id, artifact_type)
        except (OSError, RuntimeError, sqlite3.Error):
            stored = MISSING
        finally:
            with contextlib.suppress(OSError, RuntimeError, sqlite3.Error):
                db.close()
        if isinstance(stored, tuple):
            path, digest, stored_hmac = stored
            if (
                isinstance(path, str)
                and isinstance(digest, str)
                and isinstance(stored_hmac, str)
                and hmac.compare_digest(
                    stored_hmac,
                    _snapshot_hmac(receipt_secret, run_id, artifact_type, path, digest),
                )
            ):
                return path, digest
        if stored is not MISSING:
            return None
    receipt_path = _receipt_path(workspace_root, run_id, artifact_type)
    if not backend.exists(receipt_path):
        return None
    try:
        payload: object = json.loads(backend.read_text(receipt_path, encoding="utf-8"))
        return _snapshot_from_payload(payload, run_id, artifact_type, receipt_secret)
    except (OSError, json.JSONDecodeError):
        return None


def read_latest_artifact_submission_snapshot(
    workspace_root: Path,
    artifact_type: str,
    *,
    receipt_secret: str | None,
) -> tuple[str, ArtifactSubmissionSnapshot] | None:
    if receipt_secret is None:
        return None
    try:
        db = _open_db(workspace_root)
    except (OSError, RuntimeError, sqlite3.Error):
        return None
    try:
        stored = db.get_latest_receipt_submission_snapshot(artifact_type)
    except (OSError, RuntimeError, sqlite3.Error):
        return None
    finally:
        with contextlib.suppress(OSError, RuntimeError, sqlite3.Error):
            db.close()
    return _authenticate_latest_snapshot(stored, artifact_type, receipt_secret)


def _authenticate_latest_snapshot(
    stored: tuple[str, str | None, str | None, str | None, str | None] | _Missing,
    artifact_type: str,
    receipt_secret: str,
) -> tuple[str, ArtifactSubmissionSnapshot] | None:
    if not isinstance(stored, tuple):
        return None
    run_id = stored[0]
    authenticated_values: tuple[object, ...] = stored[1:]
    if not _all_strings(authenticated_values):
        return None
    receipt_hmac, path, digest, snapshot_hmac = authenticated_values
    if not hmac.compare_digest(receipt_hmac, _receipt_hmac(receipt_secret, run_id, artifact_type)):
        return None
    if not hmac.compare_digest(
        snapshot_hmac,
        _snapshot_hmac(receipt_secret, run_id, artifact_type, path, digest),
    ):
        return None
    return run_id, (path, digest)


def artifact_receipt_present(
    workspace_root: Path,
    run_id: str,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    receipt_secret: str | None = None,
) -> bool:
    try:
        db = _open_db(workspace_root)
    except (OSError, RuntimeError, sqlite3.Error):
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

    return _legacy_file_receipt_present(
        workspace_root,
        run_id,
        artifact_type,
        backend=backend,
        receipt_secret=receipt_secret,
    )


def commit_receipt_matches_changed_files(
    workspace_root: Path,
    run_id: str,
    artifact_type: str,
    changed_files: tuple[str, ...],
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> bool:
    audit_json: str | None | _Missing = MISSING
    try:
        db = _open_db(workspace_root)
        try:
            audit_json = db.get_receipt_normalization_audit(run_id, artifact_type)
        finally:
            db.close()
    except (OSError, RuntimeError, sqlite3.Error):
        pass
    if audit_json is MISSING:
        path = _receipt_path(workspace_root, run_id, artifact_type)
        if not backend.exists(path):
            return False
        try:
            payload: object = json.loads(backend.read_text(path, encoding="utf-8"))
            audit = (
                cast("dict[str, object]", payload).get("normalization_audit")
                if isinstance(payload, dict)
                else None
            )
        except (OSError, json.JSONDecodeError):
            return False
    elif audit_json is None:
        audit = None
    else:
        try:
            audit: object = json.loads(as_str(audit_json, field="normalization_audit"))
        except (json.JSONDecodeError, TypeError, ValueError):
            return False
    if not isinstance(audit, dict):
        return False
    recorded = optional_str_list(audit.get("changed_files"))
    return recorded is not None and set(recorded) == set(changed_files)


def delete_artifact_receipt(
    workspace_root: Path,
    run_id: str,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
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
    try:
        db = _open_db(workspace_root)
    except (OSError, RuntimeError, sqlite3.Error):
        db = None
    if db is not None:
        try:
            with contextlib.suppress(OSError, RuntimeError, sqlite3.Error):
                db.clear_run_receipts(run_id)
        finally:
            with contextlib.suppress(OSError, RuntimeError, sqlite3.Error):
                db.close()
    receipt_dir = _receipt_dir(workspace_root, run_id)
    for path in backend.glob(receipt_dir, "*.json"):
        backend.unlink(path, missing_ok=True)


def get_recorded_receipt_artifact_types(
    workspace_root: Path,
    run_id: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> list[str]:
    db: RunStateDB | None = None
    try:
        db = _open_db(workspace_root)
    except (OSError, RuntimeError, sqlite3.Error):
        db = None
    if db is not None:
        try:
            cursor = db._conn.execute(
                "SELECT artifact_type FROM receipts WHERE run_id = ?",
                (run_id,),
            )
            raw_rows: object = cursor.fetchall()
            if isinstance(raw_rows, list):
                result = [
                    row[0]
                    for row in cast("list[object]", raw_rows)
                    if isinstance(row, tuple) and len(row) > 0 and isinstance(row[0], str)
                ]
                if result:
                    return sorted(result)
        except (OSError, RuntimeError, sqlite3.Error):
            pass
        finally:
            with contextlib.suppress(OSError, RuntimeError, sqlite3.Error):
                db.close()
    receipt_dir = _receipt_dir(workspace_root, run_id)
    if not backend.exists(receipt_dir):
        return []
    types: list[str] = []
    for path in backend.glob(receipt_dir, "*.json"):
        try:
            parsed: object = json.loads(backend.read_text(path, encoding="utf-8"))
            if isinstance(parsed, dict):
                payload = cast("dict[str, object]", parsed)
                val = payload.get("artifact_type")
                if isinstance(val, str):
                    types.append(val)
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(types)


__all__ = [
    "RECEIPT_DIR_RELPATH_FMT",
    "ArtifactSubmissionSnapshot",
    "ReceiptPersistenceError",
    "artifact_receipt_present",
    "clear_run_receipts",
    "commit_receipt_matches_changed_files",
    "delete_artifact_receipt",
    "get_recorded_receipt_artifact_types",
    "read_artifact_submission_snapshot",
    "read_latest_artifact_submission_snapshot",
    "write_artifact_receipt",
]
