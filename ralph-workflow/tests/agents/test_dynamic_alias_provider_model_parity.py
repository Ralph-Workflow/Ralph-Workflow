from __future__ import annotations

import pytest

from ralph.agents.registry import AgentRegistry
from ralph.config.models import UnifiedConfig


@pytest.mark.parametrize(
    ("alias", "expected_model_flag"),
    [
        (
            "codex/gpt-5.3-codex",
            "--model gpt-5.3-codex",
        ),
        (
            "codex/gpt-5.3-codex[effort=xhigh]",
            "--model gpt-5.3-codex -c 'model_reasoning_effort = \"xhigh\"'",
        ),
        (
            "codex/gpt-6.1-sol[effort-low]",
            "--model gpt-6.1-sol -c 'model_reasoning_effort = \"low\"'",
        ),
        (
            "codex/provider/future-model[effort=future-tier]",
            "--model provider/future-model -c 'model_reasoning_effort = \"future-tier\"'",
        ),
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
        (
            "cursor/provider/model/family:latest",
            "--model provider/model/family:latest",
        ),
        (
            "kimi/kimi-code/k3-256k",
            "-m kimi-code/k3-256k",
        ),
    ],
)
def test_provider_model_aliases_preserve_nested_model_paths(
    alias: str, expected_model_flag: str
) -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    registry_config = registry.get(alias)
    catalog_support = registry.catalog.get(alias)

    assert registry_config is not None
    assert catalog_support is not None
    assert registry_config.model_flag == expected_model_flag
    assert catalog_support.config.model_flag == expected_model_flag


@pytest.mark.parametrize(
    "alias",
    [
        "codex/gpt-5.3-codex[effort=]",
        'codex/model[effort=high"injected]',
        "codex/model[effort=high][effort=low]",
        "opencode/provider//model",
        "nanocoder/provider//model",
        "pi/provider//model",
        "cursor/provider//model",
        "kimi/provider//model",
    ],
)
def test_provider_model_aliases_reject_empty_path_segments(alias: str) -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    assert registry.get(alias) is None
    assert registry.catalog.get(alias) is None


def test_codex_alias_keeps_clean_model_identity() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    config = registry.get("codex/gpt-6.1-sol[effort-low]")

    assert config is not None
    assert config.model == "gpt-6.1-sol"


class TestCursorAlias:
    def test_cursor_bracket_param_model_id_preserved(self) -> None:
        registry = AgentRegistry.from_config(UnifiedConfig())

        config = registry.get("cursor/claude-opus-4-8[context=1m,effort=high,fast=false]")
        catalog_support = registry.catalog.get(
            "cursor/claude-opus-4-8[context=1m,effort=high,fast=false]"
        )

        assert config is not None
        assert catalog_support is not None
        assert config.model_flag == ("--model 'claude-opus-4-8[context=1m,effort=high,fast=false]'")
        assert catalog_support.config.model_flag == (
            "--model 'claude-opus-4-8[context=1m,effort=high,fast=false]'"
        )

    def test_cursor_auto_resolves_to_model_auto(self) -> None:
        registry = AgentRegistry.from_config(UnifiedConfig())

        config = registry.get("cursor/auto")
        catalog_support = registry.catalog.get("cursor/auto")

        assert config is not None
        assert catalog_support is not None
        assert config.model_flag == "--model auto"
        assert catalog_support.config.model_flag == "--model auto"

    @pytest.mark.parametrize(
        "alias",
        [
            "cursor/",
            "cursor//",
            "cursor//x",
            "cursor/provider/",
            "cursor/provider//model",
        ],
    )
    def test_cursor_slash_empty_segments_fail_closed(self, alias: str) -> None:
        registry = AgentRegistry.from_config(UnifiedConfig())

        assert registry.get(alias) is None
        assert registry.catalog.get(alias) is None

    def test_cursor_bare_resolves_to_default_auto_routing(self) -> None:
        registry = AgentRegistry.from_config(UnifiedConfig())

        config = registry.get("cursor")
        catalog_support = registry.catalog.get("cursor")

        assert config is not None
        assert catalog_support is not None
        assert config.model_flag is None
        assert config.yolo_flag == "--yolo"
        assert config.output_flag == "--output-format stream-json"
        assert config.print_flag == "--print"
