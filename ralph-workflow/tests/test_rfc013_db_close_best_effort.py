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

import sqlite3
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.completion_signals import _db_sentinel_lookup
from ralph.agents.invoke import _clear_session_completion_sentinel
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

    target_module = sys.modules[module_name]
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

    removed = _sweep_run_state_db_rows(
        tmp_path, cutoff=0.0, keep_run_id=None
    )
    assert removed == 3
