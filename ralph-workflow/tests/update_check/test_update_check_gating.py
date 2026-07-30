"""Opt-out gating for the update nagger."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.update_check.gating import is_update_check_disabled

if TYPE_CHECKING:
    import pytest


def _isolated_env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    """An env pointing config lookups at an empty tmp dir, plus any overrides."""
    env = {"XDG_CONFIG_HOME": str(tmp_path / "config")}
    env.update(overrides)
    return env


def test_enabled_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Run from an empty cwd so the local-config walk finds nothing.
    monkeypatch.chdir(tmp_path)
    assert is_update_check_disabled(_isolated_env(tmp_path)) is False


def test_telemetry_env_disables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    env = _isolated_env(tmp_path, RALPH_DISABLE_TELEMETRY="1")
    assert is_update_check_disabled(env) is True


def test_update_check_env_disables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    env = _isolated_env(tmp_path, RALPH_DISABLE_UPDATE_CHECK="yes")
    assert is_update_check_disabled(env) is True


def test_update_check_env_ignores_non_truthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    env = _isolated_env(tmp_path, RALPH_DISABLE_UPDATE_CHECK="0")
    assert is_update_check_disabled(env) is False


def test_global_config_disables_update_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "ralph-workflow.toml").write_text(
        "[general]\nupdate_check_enabled = false\n", encoding="utf-8"
    )
    assert is_update_check_disabled(_isolated_env(tmp_path)) is True


def test_global_config_update_check_enabled_stays_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "ralph-workflow.toml").write_text(
        "[general]\nupdate_check_enabled = true\n", encoding="utf-8"
    )
    assert is_update_check_disabled(_isolated_env(tmp_path)) is False
