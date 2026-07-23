"""RFC-013 best-effort SQLite cleanup: ``db.close()`` failures must NOT surface.

Three documented best-effort helpers open ``RunStateDB`` and call ``close()``
in a ``finally`` block. Their docstrings advertise "best-effort" / "never
raises" behavior so a missing, locked, or corrupt SQLite state cannot block
start-up, gate completion, or fail cleanup.

The prior analysis feedback round surfaced that ``db.close()`` itself was
NOT inside a guarded ``finally`` block on these three call sites, so a
close()-side OSError or sqlite3.Error would propagate past the helper and
crash the caller. These tests pin the fixed contract: monkeypatch
``RunStateDB.close`` to raise after a normal lookup / prune / clear path
and assert the helper still returns its degraded-mode value.

Covers:

- ``_db_sentinel_lookup`` in ``ralph.agents.completion_signals``
- ``_clear_session_completion_sentinel`` in ``ralph.agents.invoke``
- ``_sweep_run_state_db_rows`` in ``ralph.workspace.agent_dir_retention``
"""

from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.completion_signals import _check_completion_sentinel, _db_sentinel_lookup
from ralph.agents.invoke import _clear_session_completion_sentinel
from ralph.mcp.artifacts.canonical_submit import submit_artifact_canonical
from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.mcp.artifacts.state_db import CLEARED_SENTINEL_HMAC, MISSING, RunStateDB
from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from ralph.workspace.agent_dir_retention import _sweep_run_state_db_rows

if TYPE_CHECKING:
    import pytest


class _RaisingCloseDB:
    """Stand-in for ``RunStateDB`` whose operation path succeeds but close() raises.

    Covers all three call sites uniformly without faking the real
    RunStateDB class hierarchy: the helpers only call
    ``delete_completion_sentinel`` / ``clear_run_receipts`` /
    ``prune_older_than`` / ``get_completion_sentinel_hmac`` and then
    ``close()``. The replacement routes each method by attribute name.
    """

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

    def prune_older_than(self, cutoff: float, *, keep_run_id: str | None) -> int:
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
    """Replace the module-level ``RunStateDB`` reference inside ``module_name``."""

    def _factory(workspace: Path) -> object:
        return db_factory(workspace)

    target_module = sys.modules.get(module_name)
    if target_module is None:
        target_module = importlib.import_module(module_name)
    monkeypatch.setattr(target_module, "RunStateDB", _factory)


def test_db_sentinel_lookup_swallows_close_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_db_sentinel_lookup`` advertises best-effort / never-raises behaviour.

    Even when ``RunStateDB.close()`` raises ``sqlite3.Error`` after a
    successful lookup, the helper must NOT propagate the close failure.
    The lookup's success path is preserved (the helper returns the
    looked-up value), so callers can still honour the canonical
    row before falling back to the legacy file.
    """

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
    """Operation-path failure must still degrade to ``(None, None)`` even
    when ``close()`` independently raises.

    Pinned contract: a ``sqlite3.Error`` raised by
    ``get_completion_sentinel_hmac`` returns ``(None, None)`` so the
    caller falls through to the legacy file path, while a separate
    ``close()`` raise in the ``finally`` block is suppressed and never
    overrides the degraded-mode return value.
    """

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
    """``_clear_session_completion_sentinel`` must complete the legacy-file
    cleanup even when the DB close() raises.
    """

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
    """``_sweep_run_state_db_rows`` must NOT raise even when ``close()`` raises.

    The sweep path's contract is "never raises": a missing or closed
    SQLite state must not block the rest of the ``sweep_agent_dir``
    cleanup. The ``prune_older_than`` operation path still runs and
    returns its integer row count; the ``close()`` raise that follows
    must be suppressed so the caller receives the row count cleanly.
    """

    def _factory(workspace: Path) -> _RaisingCloseDB:
        return _RaisingCloseDB(workspace, method_result=3)

    _patch_db_close(
        monkeypatch,
        "ralph.workspace.agent_dir_retention",
        db_factory=_factory,
    )

    # Pre-create the db_path the helper checks for the absence short-circuit.
    (tmp_path / ".agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".agent" / "state.db").touch()

    removed = _sweep_run_state_db_rows(tmp_path, cutoff=0.0, keep_run_id=None)
    assert removed == 3


# ---------------------------------------------------------------------------
# RFC-013 P3 durable-fallback regressions.
#
# Both regressions surfaced by the planning-decision round:
#
# 1. ``artifact._run_write_sentinel`` must fall back to the legacy
#    ``.agent/completion_seen_<run_id>.json`` file when
#    ``RunStateDB.upsert_completion_sentinel`` raises ``sqlite3.Error`` /
#    ``OSError``; otherwise a transient DB failure turns an otherwise
#    successful single-shot artifact submit into a hard failure.
#
# 2. ``_clear_session_completion_sentinel`` must tombstone the
#    DB-backed sentinel row when ``RunStateDB.delete_completion_sentinel``
#    raises so the downstream ``_check_completion_sentinel`` reader
#    honours the cleared state. Without the tombstone, a reused
#    ``run_id`` inherits the previous run's "completed" verdict because
#    the DB read is authoritative ahead of the legacy-file fallback.
# ---------------------------------------------------------------------------


class _RaisingUpsertDB:
    """Stand-in ``RunStateDB`` whose ``upsert_completion_sentinel`` raises.

    Used to simulate a transient DB write failure on the
    ``artifact._run_write_sentinel`` write path. All other write paths
    (receipts, sweep) succeed; ``close()`` is a no-op.
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self.closed = False

    def upsert_receipt(self, run_id: str, artifact_type: str, hmac_hex: str | None) -> None:
        return None

    def upsert_completion_sentinel(self, run_id: str, hmac_hex: str | None) -> None:
        raise sqlite3.OperationalError("synthetic locked db")

    def clear_run_receipts(self, run_id: str) -> None:
        return None

    def delete_completion_sentinel(self, run_id: str) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _DeleteRaisingDB:
    """Stand-in ``RunStateDB`` whose ``delete_completion_sentinel`` raises.

    Routes ``mark_completion_sentinel_cleared`` and ``clear_run_receipts``
    through a real ``RunStateDB`` so the tombstone write lands in the
    on-disk DB the reader subsequently opens.
    """

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


def test_canonical_submit_survives_completion_db_upsert_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient completion-state DB failure must not lose the artifact."""

    def _factory(workspace: Path) -> _RaisingUpsertDB:
        return _RaisingUpsertDB(workspace)

    _patch_db_close(
        monkeypatch,
        "ralph.mcp.artifacts.canonical_submit",
        db_factory=_factory,
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
    assert result.receipt_path is not None
    assert artifact_receipt_present(workspace_root, run_id, "development_result")


def test_clear_session_completion_sentinel_tombstones_on_delete_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_clear_session_completion_sentinel`` must tombstone the DB row when
    ``delete_completion_sentinel`` raises so the reader honours the cleared
    state.

    Pinned contract: ``_check_completion_sentinel`` MUST return
    ``False`` for the cleared ``run_id`` even when
    ``delete_completion_sentinel`` raised ``sqlite3.OperationalError``
    (the pre-fix bug allowed a stale DB row to survive cleanup and a
    reused ``run_id`` to be treated as completed). The fix routes the
    delete-failure path through ``mark_completion_sentinel_cleared``,
    which writes the ``CLEARED_SENTINEL_HMAC`` marker the
    ``_db_sentinel_lookup`` reader treats as ``(False, None)``.
    """
    # Seed a real DB-backed sentinel row so the reader has something
    # to disagree with after the (mocked) delete attempt.
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

    # Confirm the tombstone is visible at the SQL level (and not just
    # being interpreted by the reader).
    verify_db = RunStateDB(tmp_path)
    try:
        hmac_value = verify_db.get_completion_sentinel_hmac("run-1")
    finally:
        verify_db.close()
    assert hmac_value == CLEARED_SENTINEL_HMAC


def test_db_sentinel_lookup_treats_cleared_marker_as_absent(
    tmp_path: Path,
) -> None:
    """``_db_sentinel_lookup`` must treat the ``CLEARED_SENTINEL_HMAC``
    marker as ``(False, None)`` so ``_check_completion_sentinel``
    falls through to the legacy-file path.

    Direct unit test pinning the read-side contract that the
    tombstone fix in ``_clear_session_completion_sentinel`` relies on.
    """
    seed_db = RunStateDB(tmp_path)
    seed_db.upsert_completion_sentinel("run-cleared", CLEARED_SENTINEL_HMAC)
    seed_db.close()

    db_match, db_value = _db_sentinel_lookup(tmp_path, "run-cleared")
    assert db_match is False
    assert db_value is None


def test_mark_completion_sentinel_cleared_roundtrip(tmp_path: Path) -> None:
    """``RunStateDB.mark_completion_sentinel_cleared`` upserts the tombstone
    marker and is idempotent under repeat calls.

    Pinned contract: the tombstone write must survive subsequent
    upsert / delete attempts so a transient SQLite ``locked`` error
    that surfaces during a retry of the original delete cannot
    resurrect the cleared state.
    """
    db = RunStateDB(tmp_path)
    try:
        # Initially: no row.
        assert db.get_completion_sentinel_hmac("run-1") is MISSING

        # First tombstone write creates the row.
        db.mark_completion_sentinel_cleared("run-1")
        assert db.get_completion_sentinel_hmac("run-1") == CLEARED_SENTINEL_HMAC

        # Idempotent: a second tombstone write replaces the marker
        # (preserves the cleared state).
        db.mark_completion_sentinel_cleared("run-1")
        assert db.get_completion_sentinel_hmac("run-1") == CLEARED_SENTINEL_HMAC

        # Overwrites a prior valid HMAC row.
        db.upsert_completion_sentinel("run-1", "valid-hmac")
        assert db.get_completion_sentinel_hmac("run-1") == "valid-hmac"
        db.mark_completion_sentinel_cleared("run-1")
        assert db.get_completion_sentinel_hmac("run-1") == CLEARED_SENTINEL_HMAC
    finally:
        db.close()
