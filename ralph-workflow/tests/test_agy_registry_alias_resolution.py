"""Regression: AGY alias resolution against the real ``agy models`` wire format.

The ``tests/conftest.py`` ``_fake_agy_models_probe`` autouse fixture replaces
the probe with bare IDs, which masks the real ``agy models`` output shape
(``ID\\tDescription``). These tests override the probe with the verbatim
measured stdout so the tab-stripping in :func:`agy_published_models` is
exercised the way the live binary exercises it -- the only configuration
under which the original alias-resolution defect surfaced.
"""

from __future__ import annotations

import pytest

from ralph.agents.registry import (
    AgentRegistry,
    _parse_agy_alias,
    agy_published_models,
)
from ralph.config.models import UnifiedConfig

# Verbatim measured ``agy models`` stdout (v1.1.8+): each line is ``ID\tDescription``.
# Captured live from ``agy models`` on an authenticated account.
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
    """``agy_published_models`` keeps only the ID column from ``ID\tDescription`` lines."""
    monkeypatch.setattr(
        "ralph.agents.registry._default_agy_models_probe",
        lambda: _MEASURED_AGY_MODELS_STDOUT,
    )
    models = agy_published_models()
    # The whole point: the description column must not survive into the model set.
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
    # Measured rule (tmp/agy-source-of-truth.txt): any ``:<effort>``
    # suffix is rejected because the latest ledger concludes ``--effort``
    # is accepted only without an explicit model.
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
    """Every published alias resolves via ``registry.get`` against the real wire format."""
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
        # Measured rule: only bare published IDs are accepted; effort
        # suffixes are rejected before invocation.
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
    """Published IDs that already encode an effort tier reject ``:effort``."""
    monkeypatch.setattr(
        "ralph.agents.registry._default_agy_models_probe",
        lambda: _MEASURED_AGY_MODELS_STDOUT,
    )
    registry = AgentRegistry.from_config(UnifiedConfig())
    assert registry.get(alias) is None
