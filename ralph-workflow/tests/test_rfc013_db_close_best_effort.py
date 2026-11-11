from __future__ import annotations

import importlib
import sqlite3
import sys
from collections.abc import Iterable
from pathlib import Path

import pytest

from ralph.agents.completion_signals import _check_completion_sentinel, _db_sentinel_lookup
from ralph.agents.invoke import _clear_session_completion_sentinel
from ralph.mcp.artifacts.canonical_submit import submit_artifact_canonical
from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.mcp.artifacts.state_db import CLEARED_SENTINEL_HMAC, MISSING, RunStateDB
from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from ralph.workspace.agent_dir_retention import _sweep_run_state_db_rows

pytestmark = pytest.mark.timeout_seconds(5)


class _RaisingCloseDB:
    def __init__(
        self,
        workspace: Path,
        *,
        method_result: object = 0,
    ) -> None:
        self._workspace = workspace
        self.method_result = method_result
        self.closed = False

    def get_completion_sentinel_hmac(self, run_id: str) -> object:
        return self.method_result

    def delete_completion_sentinel(self, run_id: str) -> None:
        return None

    def clear_run_receipts(self, run_id: str) -> None:
        return None

    def prune_older_than(
        self,
        cutoff: float,
        *,
        keep_run_id: str | None = None,
        keep_run_ids: Iterable[str] = (),
    ) -> int:
        del cutoff, keep_run_id, keep_run_ids
        return int(self.method_result)

    def close(self) -> None:
        self.closed = True
        raise sqlite3.OperationalError("synthetic close failure")


def _patch_db_close(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    *,
    db_factory: object,
) -> None:

    def _factory(workspace: Path) -> object:
        return db_factory(workspace)

    target_module = sys.modules.get(module_name)
    if target_module is None:
        target_module = importlib.import_module(module_name)
    monkeypatch.setattr(target_module, "RunStateDB", _factory)


def test_db_sentinel_lookup_swallows_close_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    def _factory(workspace: Path) -> _RaisingCloseDB:
        db = _RaisingCloseDB(workspace, method_result="sentinel-hmac-value")
        return db

    _patch_db_close(
        monkeypatch,
        "ralph.agents.completion_signals",
        db_factory=_factory,
    )

    db_match, db_value = _db_sentinel_lookup(tmp_path, "run-sentinel")

    assert db_match is True
    assert db_value == "sentinel-hmac-value"


def test_db_sentinel_lookup_returns_none_when_op_raises_and_close_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    class _OpAndCloseBothFail(_RaisingCloseDB):
        def get_completion_sentinel_hmac(self, run_id: str) -> object:
            raise sqlite3.OperationalError("synthetic op failure")

    _patch_db_close(
        monkeypatch,
        "ralph.agents.completion_signals",
        db_factory=_OpAndCloseBothFail,
    )

    db_match, db_value = _db_sentinel_lookup(tmp_path, "run-sentinel")

    assert (db_match, db_value) == (None, None)


def test_clear_session_completion_sentinel_swallows_close_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    def _factory(workspace: Path) -> _RaisingCloseDB:
        return _RaisingCloseDB(workspace)

    _patch_db_close(
        monkeypatch,
        "ralph.agents.invoke",
        db_factory=_factory,
    )

    legacy_sentinel = tmp_path / ".agent" / "completion_seen_run-clear.json"
    legacy_sentinel.parent.mkdir(parents=True, exist_ok=True)
    legacy_sentinel.write_text('{"run_id": "run-clear"}', encoding="utf-8")
    assert legacy_sentinel.exists()

    _clear_session_completion_sentinel(tmp_path, "run-clear")

    assert not legacy_sentinel.exists()


def test_sweep_run_state_db_rows_does_not_raise_when_close_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    def _factory(workspace: Path) -> _RaisingCloseDB:
        return _RaisingCloseDB(workspace, method_result=3)

    _patch_db_close(
        monkeypatch,
        "ralph.workspace.agent_dir_retention",
        db_factory=_factory,
    )

    (tmp_path / ".agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".agent" / "state.db").touch()

    removed = _sweep_run_state_db_rows(tmp_path, cutoff=0.0, keep_run_ids=())
    assert removed == 3


class _DeleteRaisingDB:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._real_db: RunStateDB | None = None

    def _ensure_real_db(self) -> RunStateDB:
        if self._real_db is None:
            self._real_db = RunStateDB(self._workspace)
        return self._real_db

    def delete_completion_sentinel(self, run_id: str) -> None:
        raise sqlite3.OperationalError("synthetic locked db")

    def mark_completion_sentinel_cleared(self, run_id: str) -> None:
        self._ensure_real_db().mark_completion_sentinel_cleared(run_id)

    def clear_run_receipts(self, run_id: str) -> None:
        self._ensure_real_db().clear_run_receipts(run_id)

    def close(self) -> None:
        if self._real_db is not None:
            self._real_db.close()
            self._real_db = None


def test_canonical_submit_survives_receipt_db_upsert_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    def _raise_receipt_upsert(
        self: RunStateDB,
        run_id: str,
        artifact_type: str,
        hmac_hex: str | None,
    ) -> None:
        del self, run_id, artifact_type, hmac_hex
        raise sqlite3.OperationalError("synthetic locked db")

    monkeypatch.setattr(
        RunStateDB,
        "upsert_receipt",
        _raise_receipt_upsert,
        raising=True,
    )

    workspace_root = tmp_path
    artifact_dir = tmp_path / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    run_id = "run-write-sentinel-dbfail"
    markdown = """---
status: completed
---

## Summary

- [S-1] Durable fallback regression

## Files Changed

- [F-1] ralph/x.py

## Verification

- [V-1] Focused regression passed.
"""
    result = submit_artifact_canonical(
        workspace_root,
        "development_result",
        {},
        markdown=markdown,
        deps=ArtifactHandlerDeps(),
        run_id=run_id,
        artifact_dir=artifact_dir,
    )

    assert result.artifact_path == artifact_dir / "development_result.md"
    assert result.artifact_path.read_text(encoding="utf-8") == markdown
    legacy_receipt = workspace_root / ".agent" / "receipts" / run_id / "development_result.json"
    assert result.receipt_path == legacy_receipt
    assert legacy_receipt.exists()
    assert artifact_receipt_present(workspace_root, run_id, "development_result")


def test_clear_session_completion_sentinel_tombstones_on_delete_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_db = RunStateDB(tmp_path)
    seed_db.upsert_completion_sentinel("run-1", "real-hmac-for-run-1")
    seed_db.close()

    pre_clear = _check_completion_sentinel(tmp_path, "run-1")
    assert pre_clear is True, "precondition: reader must see seeded sentinel as present"

    def _factory(workspace: Path) -> _DeleteRaisingDB:
        return _DeleteRaisingDB(workspace)

    _patch_db_close(
        monkeypatch,
        "ralph.agents.invoke",
        db_factory=_factory,
    )

    _clear_session_completion_sentinel(tmp_path, "run-1")

    post_clear = _check_completion_sentinel(tmp_path, "run-1")
    assert post_clear is False, (
        "Reader MUST honour the cleared state even when the underlying "
        "DB delete raised; the tombstone marker guarantees this."
    )

    verify_db = RunStateDB(tmp_path)
    try:
        hmac_value = verify_db.get_completion_sentinel_hmac("run-1")
    finally:
        verify_db.close()
    assert hmac_value == CLEARED_SENTINEL_HMAC


def test_db_sentinel_lookup_treats_cleared_marker_as_absent(
    tmp_path: Path,
) -> None:
    seed_db = RunStateDB(tmp_path)
    seed_db.upsert_completion_sentinel("run-cleared", CLEARED_SENTINEL_HMAC)
    seed_db.close()

    db_match, db_value = _db_sentinel_lookup(tmp_path, "run-cleared")
    assert db_match is False
    assert db_value is None


def test_mark_completion_sentinel_cleared_roundtrip(tmp_path: Path) -> None:
    db = RunStateDB(tmp_path)
    try:
        assert db.get_completion_sentinel_hmac("run-1") is MISSING

        db.mark_completion_sentinel_cleared("run-1")
        assert db.get_completion_sentinel_hmac("run-1") == CLEARED_SENTINEL_HMAC

        db.mark_completion_sentinel_cleared("run-1")
        assert db.get_completion_sentinel_hmac("run-1") == CLEARED_SENTINEL_HMAC

        db.upsert_completion_sentinel("run-1", "valid-hmac")
        assert db.get_completion_sentinel_hmac("run-1") == "valid-hmac"
        db.mark_completion_sentinel_cleared("run-1")
        assert db.get_completion_sentinel_hmac("run-1") == CLEARED_SENTINEL_HMAC
    finally:
        db.close()
