"""Tests for ralph.skills._state_store."""

import json

import pytest

from ralph.skills._state import CapabilityEntry, CapabilityState, CapabilityStatus
from ralph.skills._state_store import (
    DEFAULT_STATE_PATH,
    default_state_path,
    load_capability_state,
    save_capability_state,
)


def test_default_state_path_is_in_home_config(tmp_path: pytest.TempdirFactory) -> None:
    path = default_state_path()
    assert path.name == "ralph-workflow-capabilities.json"
    assert ".config" in str(path)


def test_load_capability_state_returns_empty_when_file_not_found(
    tmp_path: pytest.TempdirFactory,
) -> None:
    path = tmp_path / "nonexistent.json"
    state = load_capability_state(path)
    assert isinstance(state, CapabilityState)
    assert state.web_search.status == CapabilityStatus.NOT_INSTALLED


def test_load_capability_state_returns_empty_when_file_is_corrupt(
    tmp_path: pytest.TempdirFactory,
) -> None:
    path = tmp_path / "corrupt.json"
    path.write_text("{ not valid json", encoding="utf-8")
    state = load_capability_state(path)
    assert isinstance(state, CapabilityState)


def test_save_and_load_roundtrip(tmp_path: pytest.TempdirFactory) -> None:
    state = CapabilityState(
        web_search=CapabilityEntry(
            status=CapabilityStatus.INSTALLED_HEALTHY,
            last_check_ok_iso="2025-01-01T00:00:00+00:00",
            ralph_version="1.0.0",
        ),
        skills=CapabilityEntry(
            status=CapabilityStatus.INSTALLED_HEALTHY,
            last_check_ok_iso="2025-01-01T00:00:00+00:00",
        ),
    )
    path = tmp_path / "state.json"
    save_capability_state(state, path)

    loaded = load_capability_state(path)
    assert loaded.web_search.status == CapabilityStatus.INSTALLED_HEALTHY
    assert loaded.web_search.ralph_version == "1.0.0"
    assert loaded.skills.status == CapabilityStatus.INSTALLED_HEALTHY


def test_load_capability_state_with_legacy_json_without_ralph_version(
    tmp_path: pytest.TempdirFactory,
) -> None:
    """Legacy JSON without ralph_version field should load gracefully."""
    path = tmp_path / "legacy.json"
    path.write_text(
        json.dumps({
            "web_search": {
                "status": "installed_healthy",
                "last_check_ok_iso": "2025-01-01T00:00:00+00:00",
                "last_check_fail_iso": "",
                "update_available": False,
            },
            "visit_url": {
                "status": "not_installed",
                "last_check_ok_iso": "",
                "last_check_fail_iso": "",
                "update_available": False,
            },
            "docs_mcp": {
                "status": "not_installed",
                "last_check_ok_iso": "",
                "last_check_fail_iso": "",
                "update_available": False,
            },
            "skills": {
                "status": "not_installed",
                "last_check_ok_iso": "",
                "last_check_fail_iso": "",
                "update_available": False,
            },
        }),
        encoding="utf-8",
    )
    loaded = load_capability_state(path)
    assert loaded.web_search.status == CapabilityStatus.INSTALLED_HEALTHY
    assert loaded.web_search.ralph_version == ""  # Default for missing field


def test_save_creates_parent_directories(tmp_path: pytest.TempdirFactory) -> None:
    path = tmp_path / "sub" / "deep" / "state.json"
    state = CapabilityState()
    save_capability_state(state, path)
    assert path.exists()


def test_default_state_path_constant() -> None:
    assert DEFAULT_STATE_PATH.name == "ralph-workflow-capabilities.json"
