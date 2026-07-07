"""Provider/model dynamic-alias parity across built-in model-addressable agents."""

from __future__ import annotations

import pytest

from ralph.agents.registry import AgentRegistry
from ralph.config.models import UnifiedConfig


@pytest.mark.parametrize(
    ("alias", "expected_model_flag"),
    [
        (
            "opencode/provider/model/family:latest",
            "-m provider/model/family:latest",
        ),
        (
            "nanocoder/provider/model/family:latest",
            "--provider provider --model model/family:latest",
        ),
        (
            "pi/provider/model/family:latest",
            "--model provider/model/family:latest",
        ),
    ],
)
def test_provider_model_aliases_preserve_nested_model_paths(
    alias: str, expected_model_flag: str
) -> None:
    """Every model-addressable agent must preserve nested provider/model syntax."""
    registry = AgentRegistry.from_config(UnifiedConfig())

    registry_config = registry.get(alias)
    catalog_support = registry.catalog.get(alias)

    assert registry_config is not None
    assert catalog_support is not None
    assert registry_config.model_flag == expected_model_flag
    assert catalog_support.config.model_flag == expected_model_flag
    assert registry_config.can_commit is True
    assert catalog_support.config.can_commit is True


@pytest.mark.parametrize(
    "alias",
    [
        "opencode/provider//model",
        "nanocoder/provider//model",
        "pi/provider//model",
    ],
)
def test_provider_model_aliases_reject_empty_path_segments(alias: str) -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    assert registry.get(alias) is None
    assert registry.catalog.get(alias) is None
