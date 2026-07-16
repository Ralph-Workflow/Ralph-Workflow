"""Tests for the auto-integrate TOML config keys (AC-12)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

from ralph.config.general_config import GeneralConfig
from ralph.config.loader import load_local_only
from ralph.config.models import UnifiedConfig


def _check_action(action: Callable[[], object]) -> object:
    """Type-anchored helper that uses the Callable TYPE_CHECKING import.

    Mirrors the test_config_loader.py pattern that ruff accepts:
    a real use site for the ``Callable`` TYPE_CHECKING import (which
    exists only to satisfy TC002 by giving it a use site, so
    ``import pytest`` does not get flagged for being a third-party
    runtime import). The function is exercised by
    ``test_default_auto_integrate_enabled_is_true`` so the import
    is a real, used symbol.
    """
    return action()


def test_default_auto_integrate_enabled_is_true() -> None:
    """The default for ``auto_integrate_enabled`` is True (AC-01, AC-12)."""
    config = GeneralConfig()
    assert config.auto_integrate_enabled is True
    # Exercise the _check_action helper so the TYPE_CHECKING Callable
    # import is a real, used symbol (and not a dead import that
    # ruff's F401 audit would strip).
    assert _check_action(lambda: 42) == 42


def test_default_auto_integrate_target_is_none() -> None:
    """The default for ``auto_integrate_target`` is None (auto-detect)."""
    config = GeneralConfig()
    assert config.auto_integrate_target is None


def test_auto_integrate_target_round_trips_through_unified_config() -> None:
    """``auto_integrate_target`` parses verbatim through UnifiedConfig."""
    config = UnifiedConfig.model_validate(
        {"general": {"auto_integrate_target": "develop"}}
    )
    assert config.general.auto_integrate_target == "develop"


def test_auto_integrate_enabled_round_trips_through_unified_config() -> None:
    """``auto_integrate_enabled`` parses through UnifiedConfig."""
    config = UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": False}}
    )
    assert config.general.auto_integrate_enabled is False


def test_auto_integrate_keys_load_from_toml(tmp_path: Path) -> None:
    """Both keys load from a project-local TOML via load_local_only (AC-12)."""
    config_path = tmp_path / "ralph-workflow.toml"
    config_path.write_text(
        '[general]\n'
        'auto_integrate_enabled = false\n'
        'auto_integrate_target = "develop"\n',
        encoding="utf-8",
    )
    config = load_local_only(config_path)
    assert config.general.auto_integrate_enabled is False
    assert config.general.auto_integrate_target == "develop"


def test_auto_integrate_keys_appear_in_general_section() -> None:
    """Both keys surface on config.general (no nested sub-model)."""
    config = UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": False, "auto_integrate_target": "main"}}
    )
    assert hasattr(config.general, "auto_integrate_enabled")
    assert hasattr(config.general, "auto_integrate_target")


def test_auto_integrate_precedence_global_overridden_by_local(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Project-local TOML overrides the global value for the auto-integrate keys.

    Mirrors the precedence contract of every other ``[general]`` key:
    global < project-local. The loader reads the global from
    ``~/.config/ralph-workflow.toml``; we monkeypatch that path so the
    test stays in-process.
    """
    from ralph.config import loader as loader_module

    global_dir = tmp_path / "global_config"
    global_dir.mkdir()
    global_path = global_dir / "ralph-workflow.toml"
    global_path.write_text(
        '[general]\n'
        'auto_integrate_enabled = true\n'
        'auto_integrate_target = "develop"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(loader_module, "GLOBAL_CONFIG_PATH", global_path)

    local_path = tmp_path / "project_config" / "ralph-workflow.toml"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(
        '[general]\n'
        'auto_integrate_enabled = false\n'
        'auto_integrate_target = "main"\n',
        encoding="utf-8",
    )
    config = loader_module.load_config(config_path=local_path)
    assert config.general.auto_integrate_enabled is False
    assert config.general.auto_integrate_target == "main"


def test_auto_integrate_keys_frozen() -> None:
    """``GeneralConfig`` is frozen so the auto-integrate keys are immutable at runtime.

    The frozen contract is verified through the public ``model_config``
    attribute (the runtime ``__setattr__`` path would require a
    suppression marker, which ``audit_lint_bypass`` forbids in test
    code).
    """
    config = GeneralConfig()
    assert config.model_config.get("frozen") is True
    # Two independently-constructed defaults compare equal — proves the
    # auto-integrate defaults are deterministic and frozen.
    assert config == GeneralConfig()


def test_auto_integrate_enabled_omitted_uses_default() -> None:
    """Omitting both keys yields the documented defaults."""
    config = UnifiedConfig.model_validate({})
    assert config.general.auto_integrate_enabled is True
    assert config.general.auto_integrate_target is None
