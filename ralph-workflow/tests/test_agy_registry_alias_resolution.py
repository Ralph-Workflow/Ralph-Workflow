from __future__ import annotations

import pytest

from ralph.agents.registry import (
    AgentRegistry,
    _parse_agy_alias,
    agy_published_models,
)
from ralph.config.models import UnifiedConfig

_MEASURED_AGY_MODELS_STDOUT = "\n".join(
    (
        "gemini-3.6-flash-high\tGemini 3.6 Flash (High)",
        "gemini-3.6-flash-medium\tGemini 3.6 Flash (Medium)",
        "gemini-3.6-flash-low\tGemini 3.6 Flash (Low)",
        "gemini-3.5-flash-high\tGemini 3.5 Flash (High)",
        "gemini-3.5-flash-medium\tGemini 3.5 Flash (Medium)",
        "gemini-3.5-flash-low\tGemini 3.5 Flash (Low)",
        "gemini-3.1-pro-high\tGemini 3.1 Pro (High)",
        "gemini-3.1-pro-low\tGemini 3.1 Pro (Low)",
        "claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)",
        "claude-opus-4-6-thinking\tClaude Opus 4.6 (Thinking)",
        "gpt-oss-120b-medium\tGPT-OSS 120B (Medium)",
    )
)


def test_agy_published_models_strips_tab_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ralph.agents.registry._default_agy_models_probe",
        lambda: _MEASURED_AGY_MODELS_STDOUT,
    )
    models = agy_published_models()
    assert "gemini-3.6-flash-low\tGemini 3.6 Flash (Low)" not in models
    assert "gemini-3.6-flash-low" in models
    assert all("\t" not in model for model in models), models


def test_parse_agy_alias_matches_bare_id_against_real_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ralph.agents.registry._default_agy_models_probe",
        lambda: _MEASURED_AGY_MODELS_STDOUT,
    )
    models = frozenset(agy_published_models())
    assert _parse_agy_alias("gemini-3.6-flash-low", models=models) == (
        "gemini-3.6-flash-low",
        None,
    )
    assert _parse_agy_alias("gemini-3.6-flash-low:low", models=models) is None
    assert _parse_agy_alias("claude-sonnet-4-6:low", models=models) is None
    assert _parse_agy_alias("not-published", models=models) is None


@pytest.mark.parametrize(
    "model_id",
    [
        "gemini-3.6-flash-low",
        "gemini-3.6-flash-high",
        "gemini-3.5-flash-medium",
        "gemini-3.1-pro-low",
        "claude-sonnet-4-6",
        "claude-opus-4-6-thinking",
        "gpt-oss-120b-medium",
    ],
)
def test_agy_alias_resolves_against_real_models_stdout(
    monkeypatch: pytest.MonkeyPatch,
    model_id: str,
) -> None:
    monkeypatch.setattr(
        "ralph.agents.registry._default_agy_models_probe",
        lambda: _MEASURED_AGY_MODELS_STDOUT,
    )
    registry = AgentRegistry.from_config(UnifiedConfig())
    config = registry.get(f"agy/{model_id}")
    assert config is not None, f"agy/{model_id} did not resolve against real models stdout"
    assert config.model == model_id
    assert f"--model {model_id}" in config.model_flag


def test_agy_unknown_alias_still_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ralph.agents.registry._default_agy_models_probe",
        lambda: _MEASURED_AGY_MODELS_STDOUT,
    )
    registry = AgentRegistry.from_config(UnifiedConfig())
    assert registry.get("agy/not-a-real-model") is None


@pytest.mark.parametrize(
    ("alias", "expected_flag"),
    [
        ("agy/gemini-3.6-flash-low", "--model gemini-3.6-flash-low"),
        ("agy/claude-sonnet-4-6", "--model claude-sonnet-4-6"),
    ],
)
def test_agy_synthesized_config_carries_model_flag(
    monkeypatch: pytest.MonkeyPatch,
    alias: str,
    expected_flag: str,
) -> None:
    monkeypatch.setattr(
        "ralph.agents.registry._default_agy_models_probe",
        lambda: _MEASURED_AGY_MODELS_STDOUT,
    )
    registry = AgentRegistry.from_config(UnifiedConfig())
    config = registry.get(alias)
    assert config is not None
    assert config.model_flag == expected_flag


@pytest.mark.parametrize(
    "alias",
    [
        "agy/claude-sonnet-4-6:high",
        "agy/claude-opus-4-6-thinking:low",
    ],
)
def test_agy_synthesized_config_rejects_effort_suffix(
    monkeypatch: pytest.MonkeyPatch,
    alias: str,
) -> None:
    monkeypatch.setattr(
        "ralph.agents.registry._default_agy_models_probe",
        lambda: _MEASURED_AGY_MODELS_STDOUT,
    )
    registry = AgentRegistry.from_config(UnifiedConfig())
    assert registry.get(alias) is None


@pytest.mark.parametrize(
    "alias",
    [
        "agy/gemini-3.6-flash-low:low",
        "agy/gemini-3.6-flash-high:high",
        "agy/gemini-3.5-flash-medium:medium",
    ],
)
def test_agy_tier_encoded_model_rejects_effort_suffix(
    monkeypatch: pytest.MonkeyPatch,
    alias: str,
) -> None:
    monkeypatch.setattr(
        "ralph.agents.registry._default_agy_models_probe",
        lambda: _MEASURED_AGY_MODELS_STDOUT,
    )
    registry = AgentRegistry.from_config(UnifiedConfig())
    assert registry.get(alias) is None
