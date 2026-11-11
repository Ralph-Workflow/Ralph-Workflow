from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

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
    db = RunStateDB(tmp_path)
    db.upsert_completion_sentinel("db-only", "sig")
    db.close()
    assert completion_signals_module._check_completion_sentinel(tmp_path, "db-only") is True

    legacy_dir = tmp_path / ".agent"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "completion_seen_file-only.json").write_text(
        '{"run_id": "file-only"}', encoding="utf-8"
    )
    assert completion_signals_module._check_completion_sentinel(tmp_path, "file-only") is True

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


def test_check_completion_sentinel_accepts_db_row(tmp_path: Path) -> None:
    db = RunStateDB(tmp_path)
    db.upsert_completion_sentinel("run-1", "sig-hex")
    db.close()
    assert completion_signals_module._check_completion_sentinel(tmp_path, "run-1") is True


def test_check_completion_sentinel_falls_back_to_file(tmp_path: Path) -> None:
    legacy = tmp_path / ".agent" / "completion_seen_run-2.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text('{"run_id": "run-2"}', encoding="utf-8")
    assert completion_signals_module._check_completion_sentinel(tmp_path, "run-2") is True


def test_check_completion_sentinel_db_with_secret_rejects_forged(tmp_path: Path) -> None:
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


@pytest.mark.timeout_seconds(5)
def test_check_completion_sentinel_db_with_secret_accepts_valid(tmp_path: Path) -> None:
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


def test_forged_db_sentinel_rejected_when_secret_configured(tmp_path: Path) -> None:
    db = RunStateDB(tmp_path)
    db.upsert_completion_sentinel("run-1", "totally-wrong-hmac")
    db.close()

    assert completion_signals_module._check_completion_sentinel(tmp_path, "run-1") is True
    assert (
        completion_signals_module._check_completion_sentinel(
            tmp_path, "run-1", sentinel_secret="broker-real-secret"
        )
        is False
    )


def test_forged_legacy_file_sentinel_rejected_when_secret_configured(
    tmp_path: Path,
) -> None:
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
    db = RunStateDB(tmp_path)
    db.upsert_completion_sentinel("run-1", "forged-hmac")
    db.close()

    signals_forged = _eval(tmp_path, "run-1")
    assert signals_forged.completion_sentinel_present.holds is True

    signals_real = _eval(tmp_path, "run-1", sentinel_secret="broker-real")
    assert signals_real.completion_sentinel_present.holds is False


def test_check_completion_sentinel_db_tombstone_overrides_legacy_file(
    tmp_path: Path,
) -> None:
    db = RunStateDB(tmp_path)
    db.upsert_completion_sentinel("run-1", CLEARED_SENTINEL_HMAC)
    db.close()

    legacy = tmp_path / ".agent" / "completion_seen_run-1.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        json.dumps({"run_id": "run-1", "hmac": "stale-pre-clear-value"}),
        encoding="utf-8",
    )

    assert completion_signals_module._check_completion_sentinel(tmp_path, "run-1") is False


def test_check_completion_sentinel_db_tombstone_overrides_legacy_file_with_secret(
    tmp_path: Path,
) -> None:
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
    db = RunStateDB(tmp_path)
    db.upsert_completion_sentinel("run-1", CLEARED_SENTINEL_HMAC)
    db.close()
    assert completion_signals_module._check_completion_sentinel(tmp_path, "run-1") is False


@pytest.mark.timeout_seconds(5)
def test_evaluate_completion_db_tombstone_does_not_resurrect_via_legacy_file(
    tmp_path: Path,
) -> None:
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
    assert signals.completion_sentinel_present.holds is False
