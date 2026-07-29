"""Measured AGY v1.1.8 model-alias resolution regressions."""

from __future__ import annotations

import pytest

from ralph.agents.registry import AgentRegistry
from ralph.config.models import UnifiedConfig


def test_agy_model_resolution_accepts_published_id_and_effort() -> None:
    config = AgentRegistry.from_config(UnifiedConfig()).get("agy/gemini-3.6-flash-low:medium")

    assert config is not None
    assert config.model == "gemini-3.6-flash-low"
    assert config.model_flag == "--model gemini-3.6-flash-low --effort medium"
    assert config.can_commit is True


@pytest.mark.parametrize(
    "alias",
    [
        "agy/not-published",
        "agy/gemini-3.6-flash-low:maximum",
        "agy/gemini-3.6-flash-low:low",
    ],
)
def test_agy_model_resolution_rejects_unknown_or_conflicting_alias(alias: str) -> None:
    assert AgentRegistry.from_config(UnifiedConfig()).get(alias) is None
