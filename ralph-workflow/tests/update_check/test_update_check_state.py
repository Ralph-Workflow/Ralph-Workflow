"""Cache persistence and refresh throttling for the update nagger."""

from __future__ import annotations

from pathlib import Path

from ralph.update_check.state import (
    DEFAULT_TTL_SECONDS,
    VersionCheckState,
    cache_path,
    is_refresh_due,
    load_state,
    save_state,
)


def test_cache_path_honors_xdg_cache_home() -> None:
    path = cache_path({"XDG_CACHE_HOME": "/custom/cache"})
    assert path == Path("/custom/cache/ralph-workflow/version-check.json")


def test_cache_path_defaults_to_home_cache() -> None:
    path = cache_path({})
    assert path == Path.home() / ".cache" / "ralph-workflow" / "version-check.json"


def test_save_then_load_roundtrips(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "version-check.json"
    state = VersionCheckState(last_checked=123.5, latest_version="0.9.1")
    save_state(path, state)
    assert load_state(path) == state


def test_load_missing_file_returns_none(tmp_path: Path) -> None:
    assert load_state(tmp_path / "absent.json") is None


def test_load_corrupt_file_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_state(path) is None


def test_load_rejects_non_numeric_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"last_checked": "soon", "latest_version": "1.0.0"}', encoding="utf-8")
    assert load_state(path) is None


def test_load_tolerates_null_latest_version(tmp_path: Path) -> None:
    path = tmp_path / "null.json"
    path.write_text('{"last_checked": 10.0, "latest_version": null}', encoding="utf-8")
    loaded = load_state(path)
    assert loaded == VersionCheckState(last_checked=10.0, latest_version=None)


def test_refresh_due_when_state_absent() -> None:
    assert is_refresh_due(None, now=1000.0) is True


def test_refresh_not_due_within_ttl() -> None:
    state = VersionCheckState(last_checked=1000.0, latest_version="0.9.1")
    assert is_refresh_due(state, now=1000.0 + DEFAULT_TTL_SECONDS - 1) is False


def test_refresh_due_at_ttl_boundary() -> None:
    state = VersionCheckState(last_checked=1000.0, latest_version="0.9.1")
    assert is_refresh_due(state, now=1000.0 + DEFAULT_TTL_SECONDS) is True
