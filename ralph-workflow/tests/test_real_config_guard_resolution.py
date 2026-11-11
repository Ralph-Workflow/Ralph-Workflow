from __future__ import annotations

from pathlib import Path

import pytest

from tests._real_config_guard_support import (
    _STATE,
    _extract_read_path,
)


def test_memoized_guarded_resolution_is_revalidated_mid_test(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir()
    guarded_file = guarded_dir / "ralph-workflow.toml"
    guarded_file.write_text("key = 1\n", encoding="utf-8")

    alias_dir = tmp_path / "alias_dir"
    alias_dir.mkdir()
    alias_file = tmp_path / "alias_file.toml"
    alias_file.write_text("other = 2\n", encoding="utf-8")

    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    assert _extract_read_path((str(alias_dir / "ralph-workflow.toml"), "r")) is None
    assert _extract_read_path((str(alias_file), "r")) is None

    assert str(tmp_path) in _STATE.parent_cache
    entries_after_warmup = len(_STATE.parent_cache)
    assert _extract_read_path((str(alias_dir / "other.toml"), "r")) is None
    assert len(_STATE.parent_cache) <= entries_after_warmup

    alias_dir.rmdir()
    alias_dir.symlink_to(guarded_dir)
    assert _extract_read_path((str(alias_dir / "ralph-workflow.toml"), "r")) == (
        guarded_file.resolve()
    )

    alias_file.unlink()
    alias_file.symlink_to(guarded_file)
    assert _extract_read_path((str(alias_file), "r")) == guarded_file.resolve()


def test_guarded_paths_are_reported_with_their_canonical_form(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir()
    guarded_file = guarded_dir / "ralph-workflow.toml"
    guarded_file.write_text("key = 1\n", encoding="utf-8")
    nested_dir = guarded_dir / "nested"
    nested_dir.mkdir()
    nested_file = nested_dir / "deep.toml"
    nested_file.write_text("key = 2\n", encoding="utf-8")
    alias_dir = tmp_path / "alias_dir"
    alias_dir.symlink_to(guarded_dir)
    alias_file = tmp_path / "alias_file.toml"
    alias_file.symlink_to(guarded_file)

    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    raw_paths = [
        str(guarded_file),
        str(nested_file),
        str(alias_file),
        str(alias_dir / "ralph-workflow.toml"),
        str(guarded_dir / "missing.toml"),
        f"{guarded_dir}/nested/../ralph-workflow.toml",
        f"{guarded_file}/",
    ]
    for raw_path in raw_paths:
        assert _extract_read_path((raw_path, "r")) == Path(raw_path).resolve(), raw_path
