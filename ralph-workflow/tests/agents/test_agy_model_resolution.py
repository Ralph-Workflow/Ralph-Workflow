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
        "agy/gemini-3.6-flash-low:unsupported",
    ],
)
def test_agy_model_resolution_rejects_unknown_or_conflicting_alias(alias: str) -> None:
    assert AgentRegistry.from_config(UnifiedConfig()).get(alias) is None


@pytest.mark.parametrize(
    ("alias", "model_flag"),
    [
        ("agy/gemini-3.6-flash-low:low", "--model gemini-3.6-flash-low --effort low"),
        ("agy/gemini-3.6-flash-high:high", "--model gemini-3.6-flash-high --effort high"),
    ],
)
def test_agy_model_resolution_accepts_observed_model_effort_alias(
    alias: str,
    model_flag: str,
) -> None:
    config = AgentRegistry.from_config(UnifiedConfig()).get(alias)

    assert config is not None
    assert config.model_flag == model_flag


def test_agy_model_resolution_rejection_names_published_models_and_efforts() -> None:
    help_text = agy_alias_help()

    assert "gemini-3.6-flash-low" in help_text
    assert "claude-sonnet-4-6" in help_text
    assert "low, medium, high" in help_text


def test_agy_model_resolution_regression_accepts_model_from_successful_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ralph.agents.registry._default_agy_models_probe", lambda: "account-model\n"
    )

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
