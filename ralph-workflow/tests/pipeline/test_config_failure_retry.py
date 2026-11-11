from __future__ import annotations

from pathlib import Path

import pytest

from ralph.pipeline.config_file_fingerprint import (
    ConfigFileFingerprint,
    compute_config_file_fingerprint,
    extract_config_path_from_reason,
    get_config_file_fingerprint,
    should_retry_config_failure,
)
from ralph.pipeline.config_file_fingerprint_watcher import ConfigFileFingerprintWatcher


def test_compute_config_file_fingerprint_existing_and_missing(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    assert compute_config_file_fingerprint(target) is None
    assert compute_config_file_fingerprint(None) is None

    target.write_text("initial = 1\n", encoding="utf-8")
    fp1 = compute_config_file_fingerprint(target)
    assert fp1 is not None
    assert len(fp1) == 64

    target.write_text("initial = 2\n", encoding="utf-8")
    fp2 = compute_config_file_fingerprint(target)
    assert fp2 is not None
    assert fp2 != fp1

    subdir = tmp_path / "subdir"
    subdir.mkdir()
    assert compute_config_file_fingerprint(subdir) is None


def test_get_config_file_fingerprint_named_tuple(tmp_path: Path) -> None:
    target = tmp_path / "ralph-workflow.toml"
    target.write_text("[general]\nmodel = 'test'\n", encoding="utf-8")

    fp_record = get_config_file_fingerprint(target)
    assert isinstance(fp_record, ConfigFileFingerprint)
    assert fp_record.path == target.resolve()
    assert fp_record.content_hash == compute_config_file_fingerprint(target)

    p, h = fp_record
    assert p == target.resolve()
    assert h == fp_record.content_hash


def test_extract_config_path_from_reason() -> None:
    reason = "fix /path/to/.agent/ralph-workflow.toml then run ralph --check-config"
    extracted = extract_config_path_from_reason(reason)
    assert extracted == Path("/path/to/.agent/ralph-workflow.toml")

    assert extract_config_path_from_reason(None) is None
    assert extract_config_path_from_reason("unexpected git error") is None
    assert extract_config_path_from_reason("") is None


def test_should_retry_config_failure_functional_helper(tmp_path: Path) -> None:
    cfg = tmp_path / "broken.toml"
    cfg.write_text("syntax error [", encoding="utf-8")

    initial_fp = compute_config_file_fingerprint(cfg)
    assert initial_fp is not None

    retry_needed, current_fp = should_retry_config_failure(cfg, initial_fp)
    assert retry_needed is False
    assert current_fp == initial_fp

    cfg.write_text("valid = true\n", encoding="utf-8")
    retry_needed, repaired_fp = should_retry_config_failure(cfg, initial_fp)
    assert retry_needed is True
    assert repaired_fp != initial_fp

    retry_needed, next_fp = should_retry_config_failure(cfg, repaired_fp)
    assert retry_needed is False
    assert next_fp == repaired_fp


def test_watcher_repaired_retried_once_and_unchanged_no_retry(tmp_path: Path) -> None:
    cfg = tmp_path / "ralph-workflow.toml"
    cfg.write_text("corrupt = [", encoding="utf-8")

    watcher = ConfigFileFingerprintWatcher(cfg)

    assert watcher.should_retry() is False
    assert watcher.should_retry() is False

    cfg.write_text("[general]\nmax_cycles = 10\n", encoding="utf-8")
    assert watcher.should_retry() is True

    assert watcher.should_retry() is False
    assert watcher.should_retry() is False
    assert watcher.should_retry() is False


def test_watcher_multiple_sequential_repairs(tmp_path: Path) -> None:
    cfg = tmp_path / "ralph-workflow.toml"
    cfg.write_text("bad_version_1", encoding="utf-8")

    watcher = ConfigFileFingerprintWatcher(cfg)
    assert watcher.should_retry() is False

    cfg.write_text("attempt_2", encoding="utf-8")
    assert watcher.should_retry() is True
    assert watcher.should_retry() is False

    cfg.write_text("attempt_3", encoding="utf-8")
    assert watcher.should_retry() is True
    assert watcher.should_retry() is False


def test_watcher_file_deleted_and_created_handling(tmp_path: Path) -> None:
    cfg = tmp_path / "bad.toml"
    cfg.write_text("broken = true", encoding="utf-8")

    watcher = ConfigFileFingerprintWatcher(cfg)
    assert watcher.should_retry() is False

    cfg.unlink()
    assert watcher.should_retry() is True
    assert watcher.should_retry() is False

    cfg.write_text("fixed = true\n", encoding="utf-8")
    assert watcher.should_retry() is True
    assert watcher.should_retry() is False


def test_watcher_initially_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent.toml"
    watcher = ConfigFileFingerprintWatcher(missing)

    assert watcher.should_retry() is False
    assert watcher.should_retry() is False

    missing.write_text("created = 1", encoding="utf-8")
    assert watcher.should_retry() is True
    assert watcher.should_retry() is False


def test_watcher_untracked_path_establishes_baseline(tmp_path: Path) -> None:
    cfg = tmp_path / "other.toml"
    cfg.write_text("initial", encoding="utf-8")

    watcher = ConfigFileFingerprintWatcher()
    assert watcher.should_retry(cfg) is False
    assert watcher.should_retry(cfg) is False

    cfg.write_text("repaired", encoding="utf-8")
    assert watcher.should_retry(cfg) is True
    assert watcher.should_retry(cfg) is False


def test_watcher_manual_record_failure_and_mark_attempt(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg.toml"
    cfg.write_text("content_1", encoding="utf-8")

    watcher = ConfigFileFingerprintWatcher()
    fp1 = watcher.record_failure(cfg)
    assert fp1 is not None
    assert watcher.get_last_fingerprint(cfg) == fp1
    assert watcher.get_current_fingerprint(cfg) == fp1

    assert watcher.should_retry(cfg) is False

    cfg.write_text("content_2", encoding="utf-8")
    watcher.mark_attempt(cfg)
    assert watcher.should_retry(cfg) is False


def test_watcher_reset(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg.toml"
    cfg.write_text("data_1", encoding="utf-8")

    watcher = ConfigFileFingerprintWatcher(cfg)
    cfg.write_text("data_2", encoding="utf-8")
    assert watcher.should_retry() is True
    assert watcher.should_retry() is False

    watcher.reset(cfg)
    assert watcher.should_retry() is False
    cfg.write_text("data_3", encoding="utf-8")
    assert watcher.should_retry() is True


def test_watcher_no_path_raises_value_error() -> None:
    watcher = ConfigFileFingerprintWatcher()
    with pytest.raises(ValueError, match="No path provided and no default path configured"):
        watcher.should_retry()


def test_permission_error_fingerprint_transition(tmp_path: Path) -> None:
    cfg = tmp_path / "unreadable.toml"
    cfg.write_text("sensitive = true\n", encoding="utf-8")

    normal_fp = compute_config_file_fingerprint(cfg)
    assert normal_fp is not None

    try:
        cfg.chmod(0o000)
    except OSError:
        pytest.skip("File system does not support chmod 000")

    try:
        try:
            cfg.read_bytes()
            can_read_despite_chmod = True
        except OSError:
            can_read_despite_chmod = False

        if not can_read_despite_chmod:
            unreadable_fp = compute_config_file_fingerprint(cfg)
            assert unreadable_fp is not None
            assert "unreadable" in unreadable_fp
            assert unreadable_fp != normal_fp
    finally:
        cfg.chmod(0o644)

    restored_fp = compute_config_file_fingerprint(cfg)
    assert restored_fp == normal_fp
