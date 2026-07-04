from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING

from ralph.agents import completion_signals as completion_signals_module
from ralph.mcp.artifacts.state_db import RunStateDB

if TYPE_CHECKING:
    from pathlib import Path


def test_check_completion_sentinel_returns_false_when_run_id_is_none(tmp_path: Path) -> None:
    assert completion_signals_module._check_completion_sentinel(tmp_path, None) is False


def test_check_completion_sentinel_returns_true_when_db_or_file_present(tmp_path: Path) -> None:
    """The completion gate must see a sentinel via EITHER the DB row
    OR the legacy ``.agent/completion_seen_<run>.json`` file. This
    pins the dual-read fallback contract during the rollout window.

    Case 1: only a DB row present -> True.
    Case 2: only a legacy file present -> True.
    Case 3: neither present -> False.
    """
    # Case 1: DB row only
    db = RunStateDB(tmp_path)
    db.upsert_completion_sentinel("db-only", "sig")
    db.close()
    assert (
        completion_signals_module._check_completion_sentinel(tmp_path, "db-only")
        is True
    )

    # Case 2: legacy file only
    legacy_dir = tmp_path / ".agent"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "completion_seen_file-only.json").write_text(
        '{"run_id": "file-only"}', encoding="utf-8"
    )
    assert (
        completion_signals_module._check_completion_sentinel(tmp_path, "file-only")
        is True
    )

    # Case 3: neither present
    assert (
        completion_signals_module._check_completion_sentinel(tmp_path, "absent-run")
        is False
    )


def test_check_completion_sentinel_returns_false_when_file_not_found(tmp_path: Path) -> None:
    def fake_read(_path: Path) -> str:
        raise FileNotFoundError

    assert (
        completion_signals_module._check_completion_sentinel(
            tmp_path,
            "test-run-id",
            _read_fn=fake_read,
        )
        is False
    )


def test_check_completion_sentinel_returns_true_when_file_exists(tmp_path: Path) -> None:
    assert (
        completion_signals_module._check_completion_sentinel(
            tmp_path,
            "test-run-id",
            _read_fn=lambda _path: '{"run_id": "test-run-id"}',
        )
        is True
    )


def test_check_completion_sentinel_path_ends_with_correct_filename(tmp_path: Path) -> None:
    seen: list[Path] = []

    def fake_read(path: Path) -> str:
        seen.append(path)
        return '{"run_id": "test-run-id"}'

    assert (
        completion_signals_module._check_completion_sentinel(
            tmp_path,
            "test-run-id",
            _read_fn=fake_read,
        )
        is True
    )
    assert seen == [tmp_path / ".agent" / "completion_seen_test-run-id.json"]


# RFC-013 P3: state.db-backed sentinel reads.


def test_check_completion_sentinel_accepts_db_row(tmp_path: Path) -> None:
    """A sentinel row in ``.agent/state.db`` satisfies the check
    without any ``completion_seen_<run>.json`` file on disk."""
    db = RunStateDB(tmp_path)
    db.upsert_completion_sentinel("run-1", "sig-hex")
    db.close()
    assert (
        completion_signals_module._check_completion_sentinel(tmp_path, "run-1")
        is True
    )


def test_check_completion_sentinel_falls_back_to_file(tmp_path: Path) -> None:
    """A legacy file sentinel (from a pre-upgrade release) is honored
    when the DB has no row."""
    legacy = tmp_path / ".agent" / "completion_seen_run-2.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text('{"run_id": "run-2"}', encoding="utf-8")
    assert (
        completion_signals_module._check_completion_sentinel(tmp_path, "run-2")
        is True
    )


def test_check_completion_sentinel_db_with_secret_rejects_forged(tmp_path: Path) -> None:
    """A DB row with a forged HMAC is rejected when sentinel_secret is set."""
    db = RunStateDB(tmp_path)
    db.upsert_completion_sentinel("run-1", "not-the-right-hmac")
    db.close()
    assert completion_signals_module._check_completion_sentinel(tmp_path, "run-1") is True
    assert (
        completion_signals_module._check_completion_sentinel(
            tmp_path, "run-1", sentinel_secret="real-secret"
        )
        is False
    )


def test_check_completion_sentinel_db_with_secret_accepts_valid(tmp_path: Path) -> None:
    """A DB row with a valid HMAC is accepted when sentinel_secret is set."""
    secret = "real-secret"
    run_id = "run-1"
    digest = hmac.new(secret.encode(), run_id.encode(), hashlib.sha256).hexdigest()
    db = RunStateDB(tmp_path)
    db.upsert_completion_sentinel(run_id, digest)
    db.close()
    assert (
        completion_signals_module._check_completion_sentinel(
            tmp_path, run_id, sentinel_secret=secret
        )
        is True
    )
