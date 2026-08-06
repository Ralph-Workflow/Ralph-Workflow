"""Tests for the six-key auto-integration configuration surface."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ralph.config.general_config import GeneralConfig
from ralph.config.loader import load_config, load_local_only


def test_auto_integrate_config_exposes_exactly_six_live_fields() -> None:
    """S-2: the public model admits the six supported auto-integrate keys."""
    fields = {name for name in GeneralConfig.model_fields if name.startswith("auto_integrate_")}
    assert fields == {
        "auto_integrate_enabled",
        "auto_integrate_target",
        "auto_integrate_remote_enabled",
        "auto_integrate_remote",
        "auto_integrate_remote_interval_seconds",
        "auto_integrate_reclaim_target_worktree",
    }


def test_auto_integrate_defaults_refresh_every_seam_and_reclaim_target_owner() -> None:
    """S-2: defaults enable local integration, every-seam refresh, and reclamation."""
    config = GeneralConfig()
    assert config.auto_integrate_enabled is True
    assert config.auto_integrate_target == "main"
    assert config.auto_integrate_remote_enabled is True
    assert config.auto_integrate_remote == "origin"
    assert config.auto_integrate_remote_interval_seconds == 0.0
    assert config.auto_integrate_reclaim_target_worktree is True


@pytest.mark.parametrize("field", ["auto_integrate_target", "auto_integrate_remote"])
def test_auto_integrate_branch_and_remote_reject_blank_values(field: str) -> None:
    """S-2: target and remote must be concrete, non-blank names."""
    for value in ("", "   "):
        with pytest.raises(ValidationError):
            GeneralConfig.model_validate({field: value})


@pytest.mark.parametrize("value", [-0.1, -1.0])
def test_auto_integrate_interval_rejects_negative_values(value: float) -> None:
    """S-2: the refresh interval is zero or a bounded positive duration."""
    with pytest.raises(ValidationError):
        GeneralConfig(auto_integrate_remote_interval_seconds=value)


def test_live_auto_integrate_keys_load_from_project_toml(tmp_path: Path) -> None:
    """S-2: all live keys load through the project-local configuration boundary."""
    path = tmp_path / "ralph-workflow.toml"
    path.write_text(
        "[general]\n"
        "auto_integrate_enabled = false\n"
        'auto_integrate_target = "develop"\n'
        "auto_integrate_remote_enabled = true\n"
        'auto_integrate_remote = "upstream"\n'
        "auto_integrate_remote_interval_seconds = 12.5\n"
        "auto_integrate_reclaim_target_worktree = false\n",
        encoding="utf-8",
    )

    config = load_local_only(path).general

    assert config.auto_integrate_enabled is False
    assert config.auto_integrate_target == "develop"
    assert config.auto_integrate_remote_enabled is True
    assert config.auto_integrate_remote == "upstream"
    assert config.auto_integrate_remote_interval_seconds == 12.5
    assert config.auto_integrate_reclaim_target_worktree is False


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
        'auto_integrate_remote = "upstream"\n'
        "auto_integrate_remote_interval_seconds = 5.0\n"
        "auto_integrate_reclaim_target_worktree = false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    local = tmp_path / "local.toml"
    local.write_text(
        "[general]\n"
        "auto_integrate_enabled = true\n"
        'auto_integrate_target = "main"\n'
        "auto_integrate_remote_enabled = true\n"
        'auto_integrate_remote = "origin"\n'
        "auto_integrate_remote_interval_seconds = 0.0\n"
        "auto_integrate_reclaim_target_worktree = true\n",
        encoding="utf-8",
    )

    config = load_config(config_path=local).general

    assert config == GeneralConfig()
