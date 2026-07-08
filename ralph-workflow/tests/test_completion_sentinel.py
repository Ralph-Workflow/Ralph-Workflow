from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

from ralph.agents import completion_signals as completion_signals_module
from ralph.agents.completion_signals import CompletionSignals, evaluate_completion
from ralph.mcp.artifacts.state_db import CLEARED_SENTINEL_HMAC, RunStateDB


def _eval(
    workspace: Path,
    run_id: str,
    *,
    sentinel_secret: str | None = None,
) -> CompletionSignals:
    return evaluate_completion(workspace, [], run_id=run_id, sentinel_secret=sentinel_secret)


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
    assert completion_signals_module._check_completion_sentinel(tmp_path, "db-only") is True

    # Case 2: legacy file only
    legacy_dir = tmp_path / ".agent"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "completion_seen_file-only.json").write_text(
        '{"run_id": "file-only"}', encoding="utf-8"
    )
    assert completion_signals_module._check_completion_sentinel(tmp_path, "file-only") is True

    # Case 3: neither present
    assert completion_signals_module._check_completion_sentinel(tmp_path, "absent-run") is False


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
    assert completion_signals_module._check_completion_sentinel(tmp_path, "run-1") is True


def test_check_completion_sentinel_falls_back_to_file(tmp_path: Path) -> None:
    """A legacy file sentinel (from a pre-upgrade release) is honored
    when the DB has no row."""
    legacy = tmp_path / ".agent" / "completion_seen_run-2.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text('{"run_id": "run-2"}', encoding="utf-8")
    assert completion_signals_module._check_completion_sentinel(tmp_path, "run-2") is True


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


# RFC-013 P3: forged DB/file entries must be rejected when the broker
# secret is configured at the live completion-gate verifier.


def test_forged_db_sentinel_rejected_when_secret_configured(tmp_path: Path) -> None:
    """An actor that can write to ``.agent/state.db`` but lacks the
    broker secret cannot forge a sentinel that the live verifier
    accepts."""
    db = RunStateDB(tmp_path)
    db.upsert_completion_sentinel("run-1", "totally-wrong-hmac")
    db.close()

    assert completion_signals_module._check_completion_sentinel(tmp_path, "run-1") is True
    # With a real secret configured, the forged HMAC is rejected.
    assert (
        completion_signals_module._check_completion_sentinel(
            tmp_path, "run-1", sentinel_secret="broker-real-secret"
        )
        is False
    )


def test_forged_legacy_file_sentinel_rejected_when_secret_configured(
    tmp_path: Path,
) -> None:
    """A legacy ``completion_seen_<run>.json`` file with a forged
    ``hmac`` value is rejected by the live verifier."""
    legacy = tmp_path / ".agent" / "completion_seen_run-2.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        json.dumps({"run_id": "run-2", "hmac": "forged-hmac-value"}),
        encoding="utf-8",
    )

    assert completion_signals_module._check_completion_sentinel(tmp_path, "run-2") is True
    assert (
        completion_signals_module._check_completion_sentinel(
            tmp_path, "run-2", sentinel_secret="broker-real-secret"
        )
        is False
    )


def test_evaluate_completion_threads_sentinel_secret_to_verifier(
    tmp_path: Path,
) -> None:
    """End-to-end: ``evaluate_completion`` with ``sentinel_secret``
    rejects a forged DB row. Pins the live-wiring contract that the
    orchestrator can enable HMAC enforcement by setting
    ``opts.sentinel_secret``."""
    db = RunStateDB(tmp_path)
    db.upsert_completion_sentinel("run-1", "forged-hmac")
    db.close()

    # Without a secret, the forged DB row is accepted (pre-P3 contract).
    signals_forged = _eval(tmp_path, "run-1")
    assert signals_forged.completion_sentinel_present is True

    # With a real secret configured, the forged DB row is rejected.
    signals_real = _eval(tmp_path, "run-1", sentinel_secret="broker-real")
    assert signals_real.completion_sentinel_present is False


# RFC-013 P3 regression: a DB tombstone (CLEARED_SENTINEL_HMAC) MUST be
# terminal. A stale ``completion_seen_<run>.json`` file left over from
# before the clear MUST NOT resurrect a reused ``run_id``'s "completed"
# verdict, otherwise a clear-then-reuse cycle leaks prior-run state.


def test_check_completion_sentinel_db_tombstone_overrides_legacy_file(
    tmp_path: Path,
) -> None:
    """A tombstoned DB row (``hmac == CLEARED_SENTINEL_HMAC``) must
    return ``False`` even when a legacy ``completion_seen_<run>.json``
    file exists on disk. The clear attempt is authoritative; the
    legacy file is stale.
    """
    db = RunStateDB(tmp_path)
    db.upsert_completion_sentinel("run-1", CLEARED_SENTINEL_HMAC)
    db.close()

    # Plant a stale legacy sentinel that would otherwise indicate completion.
    legacy = tmp_path / ".agent" / "completion_seen_run-1.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        json.dumps({"run_id": "run-1", "hmac": "stale-pre-clear-value"}),
        encoding="utf-8",
    )

    # With no secret: tombstone still wins; the legacy file MUST NOT
    # resurrect the "completed" verdict.
    assert completion_signals_module._check_completion_sentinel(tmp_path, "run-1") is False


def test_check_completion_sentinel_db_tombstone_overrides_legacy_file_with_secret(
    tmp_path: Path,
) -> None:
    """Same as above with a sentinel_secret configured — the tombstone
    is terminal regardless of HMAC verification settings.
    """
    db = RunStateDB(tmp_path)
    db.upsert_completion_sentinel("run-1", CLEARED_SENTINEL_HMAC)
    db.close()

    legacy = tmp_path / ".agent" / "completion_seen_run-1.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        json.dumps({"run_id": "run-1", "hmac": "stale-pre-clear-value"}),
        encoding="utf-8",
    )

    assert (
        completion_signals_module._check_completion_sentinel(
            tmp_path, "run-1", sentinel_secret="broker-real"
        )
        is False
    )


def test_check_completion_sentinel_db_tombstone_alone_returns_false(
    tmp_path: Path,
) -> None:
    """A tombstone with NO legacy file on disk must return ``False``
    (no spurious resurrection via either path)."""
    db = RunStateDB(tmp_path)
    db.upsert_completion_sentinel("run-1", CLEARED_SENTINEL_HMAC)
    db.close()
    assert completion_signals_module._check_completion_sentinel(tmp_path, "run-1") is False


def test_evaluate_completion_db_tombstone_does_not_resurrect_via_legacy_file(
    tmp_path: Path,
) -> None:
    """End-to-end: ``evaluate_completion`` with a tombstoned DB row and
    a stale legacy file reports ``completion_sentinel_present=False``,
    not ``True``. Pins the live-wiring contract that the orchestrator
    cannot be tricked into accepting a reused ``run_id`` as already
    completed."""
    db = RunStateDB(tmp_path)
    db.upsert_completion_sentinel("run-1", CLEARED_SENTINEL_HMAC)
    db.close()

    legacy = tmp_path / ".agent" / "completion_seen_run-1.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        json.dumps({"run_id": "run-1"}),
        encoding="utf-8",
    )

    signals = _eval(tmp_path, "run-1", sentinel_secret="broker-real")
    assert signals.completion_sentinel_present is False
