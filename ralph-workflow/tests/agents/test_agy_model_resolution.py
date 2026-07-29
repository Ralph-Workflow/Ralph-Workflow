"""Measured AGY v1.1.8 model-alias resolution regressions."""

from __future__ import annotations

import pytest

from ralph.agents.registry import AgentRegistry, agy_alias_help
from ralph.config.models import UnifiedConfig


@pytest.mark.parametrize(
    "alias",
    [
        "agy/not-published",
        "agy/gemini-3.6-flash-low:maximum",
        "agy/gemini-3.6-flash-low:low",
        "agy/gemini-3.6-flash-high:low",
        "agy/gpt-oss-120b-medium:high",
        "agy/claude-sonnet-4-6:high",
    ],
)
def test_agy_model_resolution_rejects_unknown_or_conflicting_alias(alias: str) -> None:
    assert AgentRegistry.from_config(UnifiedConfig()).get(alias) is None


def test_agy_model_resolution_rejection_names_published_models_and_efforts() -> None:
    help_text = agy_alias_help()

    assert "gemini-3.6-flash-low" in help_text
    assert "claude-sonnet-4-6" in help_text
    assert "low, medium, high" in help_text


def test_agy_model_resolution_regression_accepts_model_from_successful_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ralph.agents.registry._default_agy_models_probe", lambda: "account-model\n")

    assert AgentRegistry.from_config(UnifiedConfig()).get("agy/account-model") is not None


def test_agy_model_resolution_regression_falls_back_when_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_probe() -> str:
        raise OSError("agy unavailable")

    monkeypatch.setattr("ralph.agents.registry._default_agy_models_probe", failing_probe)

    registry = AgentRegistry.from_config(UnifiedConfig())
    assert registry.get("agy/gemini-3.6-flash-low") is not None
    assert registry.get("agy/not-published") is None
