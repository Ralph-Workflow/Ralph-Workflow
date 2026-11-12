from __future__ import annotations

from pathlib import Path

import pytest

from ralph.skills.gating import (
    UNIVERSAL_SKILL_UPDATE_DISABLE_ENV,
    is_universal_skill_update_disabled,
    universal_skill_updates_enabled,
)


def _config_env(config_dir: Path, body: str | None = None) -> dict[str, str]:
    config_dir.mkdir(parents=True, exist_ok=True)
    if body is not None:
        (config_dir / "ralph-workflow.toml").write_text(body, encoding="utf-8")
    return {"XDG_CONFIG_HOME": str(config_dir)}


def test_env_absent_leaves_updates_enabled(tmp_path: Path) -> None:
    env = _config_env(tmp_path / "config")
    assert is_universal_skill_update_disabled(env) is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", " On "])
def test_truthy_env_value_disables_updates(tmp_path: Path, value: str) -> None:
    env = _config_env(tmp_path / "config")
    env[UNIVERSAL_SKILL_UPDATE_DISABLE_ENV] = value
    assert is_universal_skill_update_disabled(env) is True


@pytest.mark.parametrize("value", ["0", "false", "no", ""])
def test_non_truthy_env_value_leaves_updates_enabled(tmp_path: Path, value: str) -> None:
    env = _config_env(tmp_path / "config")
    env[UNIVERSAL_SKILL_UPDATE_DISABLE_ENV] = value
    assert is_universal_skill_update_disabled(env) is False


def test_config_false_disables_updates(tmp_path: Path) -> None:
    env = _config_env(
        tmp_path / "config",
        "[skills]\nuniversal_updates_enabled = false\n",
    )
    assert is_universal_skill_update_disabled(env) is True


def test_config_true_leaves_updates_enabled(tmp_path: Path) -> None:
    env = _config_env(
        tmp_path / "config",
        "[skills]\nuniversal_updates_enabled = true\n",
    )
    assert is_universal_skill_update_disabled(env) is False


@pytest.mark.parametrize("literal", ['"false"', "0", '"no"'])
def test_config_non_boolean_value_leaves_updates_enabled(tmp_path: Path, literal: str) -> None:
    env = _config_env(
        tmp_path / "config",
        f"[skills]\nuniversal_updates_enabled = {literal}\n",
    )
    assert is_universal_skill_update_disabled(env) is False


def test_missing_config_file_leaves_updates_enabled(tmp_path: Path) -> None:
    env = _config_env(tmp_path / "config")
    assert is_universal_skill_update_disabled(env) is False


def test_malformed_config_leaves_updates_enabled(tmp_path: Path) -> None:
    env = _config_env(
        tmp_path / "config",
        "[skills\nuniversal_updates_enabled = false\n",
    )
    assert is_universal_skill_update_disabled(env) is False


def test_missing_skills_table_leaves_updates_enabled(tmp_path: Path) -> None:
    env = _config_env(
        tmp_path / "config",
        "[general]\nupdate_check_enabled = false\n",
    )
    assert is_universal_skill_update_disabled(env) is False


def test_missing_config_key_leaves_updates_enabled(tmp_path: Path) -> None:
    env = _config_env(
        tmp_path / "config",
        "[skills]\nsome_other_key = false\n",
    )
    assert is_universal_skill_update_disabled(env) is False


def test_xdg_config_home_is_honoured(tmp_path: Path) -> None:
    env = _config_env(
        tmp_path / "xdg",
        "[skills]\nuniversal_updates_enabled = false\n",
    )
    assert is_universal_skill_update_disabled(env) is True


def test_xdg_config_home_wins_over_the_home_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    (home / ".config" / "ralph-workflow.toml").write_text(
        "[skills]\nuniversal_updates_enabled = false\n", encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    env = _config_env(tmp_path / "empty-xdg")
    assert is_universal_skill_update_disabled(env) is False


def test_home_config_is_used_when_xdg_config_home_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    (home / ".config" / "ralph-workflow.toml").write_text(
        "[skills]\nuniversal_updates_enabled = false\n", encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    assert is_universal_skill_update_disabled({}) is True


def test_env_opt_out_applies_even_when_the_config_enables_updates(tmp_path: Path) -> None:
    env = _config_env(
        tmp_path / "config",
        "[skills]\nuniversal_updates_enabled = true\n",
    )
    env[UNIVERSAL_SKILL_UPDATE_DISABLE_ENV] = "1"
    assert is_universal_skill_update_disabled(env) is True


def test_config_opt_out_applies_without_the_env_var(tmp_path: Path) -> None:
    env = _config_env(
        tmp_path / "config",
        "[skills]\nuniversal_updates_enabled = false\n",
    )
    assert UNIVERSAL_SKILL_UPDATE_DISABLE_ENV not in env
    assert is_universal_skill_update_disabled(env) is True


def test_default_environ_reads_the_process_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _config_env(tmp_path / "config")
    monkeypatch.setenv("XDG_CONFIG_HOME", env["XDG_CONFIG_HOME"])
    monkeypatch.setenv(UNIVERSAL_SKILL_UPDATE_DISABLE_ENV, "1")
    assert is_universal_skill_update_disabled() is True


def test_default_environ_reads_the_xdg_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _config_env(
        tmp_path / "config",
        "[skills]\nuniversal_updates_enabled = false\n",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", env["XDG_CONFIG_HOME"])
    assert is_universal_skill_update_disabled() is True


def test_updates_enabled_helper_is_the_inverse(tmp_path: Path) -> None:
    disabled = _config_env(
        tmp_path / "disabled",
        "[skills]\nuniversal_updates_enabled = false\n",
    )
    enabled = _config_env(tmp_path / "enabled")
    assert universal_skill_updates_enabled(disabled) is False
    assert universal_skill_updates_enabled(enabled) is True
