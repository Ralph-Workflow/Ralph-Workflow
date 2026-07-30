"""Tests for measured ``agy/<model>[:effort]`` dynamic aliases."""

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
        "agy/gemini-3.6-flash-high",
        "agy/gemini-3.6-flash-medium",
        "agy/gemini-3.6-flash-low",
        "agy/gemini-3.5-flash-high",
        "agy/gemini-3.5-flash-medium",
        "agy/gemini-3.5-flash-low",
        "agy/gemini-3.1-pro-high",
        "agy/gemini-3.1-pro-low",
        "agy/claude-sonnet-4-6",
        "agy/claude-opus-4-6-thinking",
        "agy/gpt-oss-120b-medium",
    ],
)
def test_agy_model_alias_sets_published_model_flag_with_commit_permission(name: str) -> None:
    config = AgentRegistry.from_config(UnifiedConfig()).get(name)

    assert config is not None
    assert config.model_flag == f"--model {shlex.quote(name.removeprefix('agy/'))}"
    assert config.can_commit is True


@pytest.mark.parametrize("name", ["agy", "agy/", "agy/not-published"])
def test_agy_model_alias_rejects_unknown_names(name: str) -> None:
    assert _resolve_dynamic_agent(name, CcsConfig()) is None


def test_agy_model_alias_preserves_agy_transport() -> None:
    config = AgentRegistry.from_config(UnifiedConfig()).get("agy/claude-sonnet-4-6")

    assert config is not None
    assert config.transport == AgentTransport.AGY
