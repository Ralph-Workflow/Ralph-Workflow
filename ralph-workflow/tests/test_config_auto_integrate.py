"""Tests for the four-key auto-integration configuration surface."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ralph.config.general_config import GeneralConfig
from ralph.config.loader import load_config, load_local_only


def test_auto_integrate_config_exposes_exactly_four_live_fields() -> None:
    """S-1: the public model admits only the four supported auto-integrate keys."""
    fields = {name for name in GeneralConfig.model_fields if name.startswith("auto_integrate_")}
    assert fields == {
        "auto_integrate_enabled",
        "auto_integrate_target",
        "auto_integrate_remote_enabled",
        "auto_integrate_remote",
    }


def test_auto_integrate_defaults_are_local_main_without_remote_sync() -> None:
    """S-1: defaults enable local integration against main only."""
    config = GeneralConfig()
    assert config.auto_integrate_enabled is True
    assert config.auto_integrate_target == "main"
    assert config.auto_integrate_remote_enabled is False
    assert config.auto_integrate_remote == "origin"


@pytest.mark.parametrize("field", ["auto_integrate_target", "auto_integrate_remote"])
def test_auto_integrate_branch_and_remote_reject_blank_values(field: str) -> None:
    """S-2: target and remote must be concrete, non-blank names."""
    for value in ("", "   "):
        with pytest.raises(ValidationError):
            GeneralConfig.model_validate({field: value})


def test_live_auto_integrate_keys_load_from_project_toml(tmp_path: Path) -> None:
    """S-2: all live keys load through the project-local configuration boundary."""
    path = tmp_path / "ralph-workflow.toml"
    path.write_text(
        "[general]\n"
        "auto_integrate_enabled = false\n"
        'auto_integrate_target = "develop"\n'
        "auto_integrate_remote_enabled = true\n"
        'auto_integrate_remote = "upstream"\n',
        encoding="utf-8",
    )

    config = load_local_only(path).general

    assert config.auto_integrate_enabled is False
    assert config.auto_integrate_target == "develop"
    assert config.auto_integrate_remote_enabled is True
    assert config.auto_integrate_remote == "upstream"


def test_live_auto_integrate_local_values_override_global(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """S-2: project-local live keys retain normal global-to-local precedence."""
    config_home = tmp_path / "config"
    config_home.mkdir()
    (config_home / "ralph-workflow.toml").write_text(
        "[general]\n"
        "auto_integrate_enabled = false\n"
        'auto_integrate_target = "develop"\n'
        "auto_integrate_remote_enabled = true\n"
        'auto_integrate_remote = "upstream"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    local = tmp_path / "local.toml"
    local.write_text(
        "[general]\n"
        "auto_integrate_enabled = true\n"
        'auto_integrate_target = "main"\n'
        "auto_integrate_remote_enabled = false\n"
        'auto_integrate_remote = "origin"\n',
        encoding="utf-8",
    )

    config = load_config(config_path=local).general

    assert config == GeneralConfig()
