"""Regression tests for legacy provider_fallback guidance."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.loader import GLOBAL_CONFIG_PATH, LOCAL_CONFIG_PATH, load_config

if TYPE_CHECKING:
    import pytest


def _capture_warnings(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    warnings: list[str] = []

    def capture_warning(message: str) -> None:
        warnings.append(message)

    monkeypatch.setattr("ralph.config.loader.logger.warning", capture_warning)
    return warnings


def test_provider_fallback_regression_warns_when_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """S-1: legacy provider_fallback points users to active chain fallback."""
    global_path = tmp_path / GLOBAL_CONFIG_PATH.name
    global_path.write_text(
        "[general]\nprovider_fallback = { claude = [\"codex\"] }\n", encoding="utf-8"
    )
    monkeypatch.setattr("ralph.config.loader.GLOBAL_CONFIG_PATH", global_path)
    monkeypatch.setattr("ralph.config.loader.LOCAL_CONFIG_PATH", tmp_path / LOCAL_CONFIG_PATH.name)
    warnings = _capture_warnings(monkeypatch)

    load_config(config_path=tmp_path / LOCAL_CONFIG_PATH.name)

    assert len(warnings) == 1
    assert "read by nothing" in warnings[0]
    assert "delete it" in warnings[0]
    assert "[agent_chains]" in warnings[0]


def test_provider_fallback_regression_empty_value_warns_on_presence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """S-3: an empty legacy knob still warns because it does nothing."""
    global_path = tmp_path / GLOBAL_CONFIG_PATH.name
    global_path.write_text("[general]\nprovider_fallback = {}\n", encoding="utf-8")
    monkeypatch.setattr("ralph.config.loader.GLOBAL_CONFIG_PATH", global_path)
    monkeypatch.setattr("ralph.config.loader.LOCAL_CONFIG_PATH", tmp_path / LOCAL_CONFIG_PATH.name)
    warnings = _capture_warnings(monkeypatch)

    load_config(config_path=tmp_path / LOCAL_CONFIG_PATH.name)

    assert len(warnings) == 1
    assert "read by nothing" in warnings[0]
    assert "delete it" in warnings[0]
