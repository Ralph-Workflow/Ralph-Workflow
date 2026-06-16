"""Tests for the agy/<model> dynamic agent resolver."""

from __future__ import annotations

import shlex

import pytest

from ralph.agents.registry import AgentRegistry, _resolve_dynamic_agent
from ralph.config.ccs_config import CcsConfig
from ralph.config.enums import AgentTransport
from ralph.config.models import UnifiedConfig


@pytest.mark.parametrize(
    "name",
    [
        "agy/Gemini 3.5 Flash (Medium)",
        "agy/Gemini 3.5 Flash (High)",
        "agy/Gemini 3.5 Flash (Low)",
        "agy/Gemini 3.1 Pro (Low)",
        "agy/Gemini 3.1 Pro (High)",
        "agy/Claude Sonnet 4.6 (Thinking)",
        "agy/Claude Opus 4.6 (Thinking)",
        "agy/GPT-OSS 120B (Medium)",
    ],
)
def test_agy_model_alias_sets_model_flag_and_can_commit(name: str) -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())
    config = registry.get(name)

    assert config is not None
    assert config.model_flag == f"--model {shlex.quote(name.removeprefix('agy/'))}"
    assert config.can_commit is True


@pytest.mark.parametrize("name", ["agy", "agy/"])
def test_agy_model_alias_rejects_short_names(name: str) -> None:
    assert _resolve_dynamic_agent(name, CcsConfig()) is None


def test_agy_model_alias_preserves_agy_transport() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())
    config = registry.get("agy/Claude Sonnet 4.6 (Thinking)")

    assert config is not None
    assert config.transport == AgentTransport.AGY
