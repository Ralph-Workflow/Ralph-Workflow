"""Kill-switch gating for universal-skill updates.

The gate answers one question: are universal-skill updates disabled?
Two INDEPENDENT controls answer it — the ``RALPH_DISABLE_SKILL_UPDATES``
environment variable and the ``[skills] universal_updates_enabled`` key
in the user-global config file. Every test injects the environment
mapping and keeps its config file under ``tmp_path``, so no test reads
or writes the real home directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.skills.gating import (
    UNIVERSAL_SKILL_UPDATE_DISABLE_ENV,
    is_universal_skill_update_disabled,
    universal_skill_updates_enabled,
)


def _config_env(config_dir: Path, body: str | None = None) -> dict[str, str]:
    """Return an env whose config lookup points at ``config_dir``.

    ``body`` is the TOML content of ``ralph-workflow.toml``; ``None``
    leaves the file absent. The caller owns the directory name, so two
    calls never share a config file.
    """
    config_dir.mkdir(parents=True, exist_ok=True)
    if body is not None:
        (config_dir / "ralph-workflow.toml").write_text(body, encoding="utf-8")
    return {"XDG_CONFIG_HOME": str(config_dir)}


def test_env_absent_leaves_updates_enabled(tmp_path: Path) -> None:
    """No opt-out anywhere means the updates stay enabled."""
    env = _config_env(tmp_path / "config")
    assert is_universal_skill_update_disabled(env) is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", " On "])
def test_truthy_env_value_disables_updates(tmp_path: Path, value: str) -> None:
    """A truthy env value disables, whatever its case or padding."""
    env = _config_env(tmp_path / "config")
    env[UNIVERSAL_SKILL_UPDATE_DISABLE_ENV] = value
    assert is_universal_skill_update_disabled(env) is True


@pytest.mark.parametrize("value", ["0", "false", "no", ""])
def test_non_truthy_env_value_leaves_updates_enabled(tmp_path: Path, value: str) -> None:
    """A non-truthy env value is not an opt-out."""
    env = _config_env(tmp_path / "config")
    env[UNIVERSAL_SKILL_UPDATE_DISABLE_ENV] = value
    assert is_universal_skill_update_disabled(env) is False


def test_config_false_disables_updates(tmp_path: Path) -> None:
    """``universal_updates_enabled = false`` disables the updates."""
    env = _config_env(
        tmp_path / "config",
        "[skills]\nuniversal_updates_enabled = false\n",
    )
    assert is_universal_skill_update_disabled(env) is True


def test_config_true_leaves_updates_enabled(tmp_path: Path) -> None:
    """``universal_updates_enabled = true`` is not an opt-out."""
    env = _config_env(
        tmp_path / "config",
        "[skills]\nuniversal_updates_enabled = true\n",
    )
    assert is_universal_skill_update_disabled(env) is False


@pytest.mark.parametrize("literal", ['"false"', "0", '"no"'])
def test_config_non_boolean_value_leaves_updates_enabled(tmp_path: Path, literal: str) -> None:
    """Only the boolean ``false`` disables; a non-boolean value does not."""
    env = _config_env(
        tmp_path / "config",
        f"[skills]\nuniversal_updates_enabled = {literal}\n",
    )
    assert is_universal_skill_update_disabled(env) is False


def test_missing_config_file_leaves_updates_enabled(tmp_path: Path) -> None:
    """An absent config file is not an opt-out."""
    env = _config_env(tmp_path / "config")
    assert is_universal_skill_update_disabled(env) is False


def test_malformed_config_leaves_updates_enabled(tmp_path: Path) -> None:
    """Malformed TOML fails open instead of raising."""
    env = _config_env(
        tmp_path / "config",
        "[skills\nuniversal_updates_enabled = false\n",
    )
    assert is_universal_skill_update_disabled(env) is False


def test_missing_skills_table_leaves_updates_enabled(tmp_path: Path) -> None:
    """A config without a ``[skills]`` table is not an opt-out."""
    env = _config_env(
        tmp_path / "config",
        "[general]\nupdate_check_enabled = false\n",
    )
    assert is_universal_skill_update_disabled(env) is False


def test_missing_config_key_leaves_updates_enabled(tmp_path: Path) -> None:
    """A ``[skills]`` table without the key is not an opt-out."""
    env = _config_env(
        tmp_path / "config",
        "[skills]\nsome_other_key = false\n",
    )
    assert is_universal_skill_update_disabled(env) is False


def test_xdg_config_home_is_honoured(tmp_path: Path) -> None:
    """The config is read from ``$XDG_CONFIG_HOME`` when that is set."""
    env = _config_env(
        tmp_path / "xdg",
        "[skills]\nuniversal_updates_enabled = false\n",
    )
    assert is_universal_skill_update_disabled(env) is True


def test_xdg_config_home_wins_over_the_home_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``$XDG_CONFIG_HOME`` is preferred over ``~/.config``."""
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
    """Without ``$XDG_CONFIG_HOME`` the config comes from ``~/.config``."""
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    (home / ".config" / "ralph-workflow.toml").write_text(
        "[skills]\nuniversal_updates_enabled = false\n", encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    assert is_universal_skill_update_disabled({}) is True


def test_env_opt_out_applies_even_when_the_config_enables_updates(tmp_path: Path) -> None:
    """The env opt-out works on its own."""
    env = _config_env(
        tmp_path / "config",
        "[skills]\nuniversal_updates_enabled = true\n",
    )
    env[UNIVERSAL_SKILL_UPDATE_DISABLE_ENV] = "1"
    assert is_universal_skill_update_disabled(env) is True


def test_config_opt_out_applies_without_the_env_var(tmp_path: Path) -> None:
    """The config opt-out works on its own."""
    env = _config_env(
        tmp_path / "config",
        "[skills]\nuniversal_updates_enabled = false\n",
    )
    assert UNIVERSAL_SKILL_UPDATE_DISABLE_ENV not in env
    assert is_universal_skill_update_disabled(env) is True


def test_default_environ_reads_the_process_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting ``environ`` falls back to the process environment."""
    env = _config_env(tmp_path / "config")
    monkeypatch.setenv("XDG_CONFIG_HOME", env["XDG_CONFIG_HOME"])
    monkeypatch.setenv(UNIVERSAL_SKILL_UPDATE_DISABLE_ENV, "1")
    assert is_universal_skill_update_disabled() is True


def test_default_environ_reads_the_xdg_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting ``environ`` still reads the injected config location."""
    env = _config_env(
        tmp_path / "config",
        "[skills]\nuniversal_updates_enabled = false\n",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", env["XDG_CONFIG_HOME"])
    assert is_universal_skill_update_disabled() is True


def test_updates_enabled_helper_is_the_inverse(tmp_path: Path) -> None:
    """``universal_skill_updates_enabled`` mirrors the disabled predicate."""
    disabled = _config_env(
        tmp_path / "disabled",
        "[skills]\nuniversal_updates_enabled = false\n",
    )
    enabled = _config_env(tmp_path / "enabled")
    assert universal_skill_updates_enabled(disabled) is False
    assert universal_skill_updates_enabled(enabled) is True
