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
        (
            "cursor/provider/model/family:latest",
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
        "cursor/provider//model",
    ],
)
def test_provider_model_aliases_reject_empty_path_segments(alias: str) -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    assert registry.get(alias) is None
    assert registry.catalog.get(alias) is None


class TestCursorAlias:
    """Cursor-specific dynamic-alias tests (8th built-in)."""

    def test_cursor_bracket_param_model_id_preserved(self) -> None:
        """Bracket-parameterized Cursor model ids are preserved verbatim.

        Cursor's documented model catalog supports bracket
        parameterization for the documented model id
        ``claude-opus-4-8[context=1m,effort=high,fast=false]`` (an
        example from the operator-facing docs).  The full suffix
        after ``cursor/`` MUST be preserved in the ``--model`` flag.
        """
        registry = AgentRegistry.from_config(UnifiedConfig())

        config = registry.get("cursor/claude-opus-4-8[context=1m,effort=high,fast=false]")
        catalog_support = registry.catalog.get(
            "cursor/claude-opus-4-8[context=1m,effort=high,fast=false]"
        )

        assert config is not None
        assert catalog_support is not None
        # The full bracket-parameterized id is preserved (with
        # ``shlex.quote`` so the brackets do not tokenize into
        # extra argv tokens at command-build time).
        assert config.model_flag == (
            "--model 'claude-opus-4-8[context=1m,effort=high,fast=false]'"
        )
        assert catalog_support.config.model_flag == (
            "--model 'claude-opus-4-8[context=1m,effort=high,fast=false]'"
        )
        assert config.can_commit is True
        assert catalog_support.config.can_commit is True

    def test_cursor_auto_resolves_to_model_auto(self) -> None:
        """``cursor/auto`` is the explicit Auto alias.

        Cursor's documented default routing model is ``auto`` (the
        ``Auto (current, default)`` entry in ``agent models``).  The
        alias MUST resolve to ``--model auto`` so operators can
        spell Auto out in chains and diagnostics without
        losing the documented default-routing semantics.
        """
        registry = AgentRegistry.from_config(UnifiedConfig())

        config = registry.get("cursor/auto")
        catalog_support = registry.catalog.get("cursor/auto")

        assert config is not None
        assert catalog_support is not None
        assert config.model_flag == "--model auto"
        assert catalog_support.config.model_flag == "--model auto"
        assert config.can_commit is True

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
        """Empty / ambiguous cursor/<model> shapes fail closed.

        The resolver MUST return ``None`` for shapes that would
        create empty or ambiguous argv values (and would silently
        route a wrong model):

          * ``cursor/`` -- empty model id
          * ``cursor//`` -- empty model id (after the cursor/ prefix)
          * ``cursor//x`` -- empty segment before the first content
          * ``cursor/provider/`` -- empty model after the slash
          * ``cursor/provider//model`` -- empty segment mid-path
        """
        registry = AgentRegistry.from_config(UnifiedConfig())

        assert registry.get(alias) is None
        assert registry.catalog.get(alias) is None

    def test_cursor_bare_resolves_to_default_auto_routing(self) -> None:
        """Bare ``cursor`` resolves to the built-in Auto default routing.

        ``cursor`` alone (no ``/<model>``) MUST resolve to the
        built-in's default ``--yolo + Auto routing`` config
        (no ``--model`` override; Cursor picks the default
        ``auto`` model at runtime).
        """
        registry = AgentRegistry.from_config(UnifiedConfig())

        config = registry.get("cursor")
        catalog_support = registry.catalog.get("cursor")

        assert config is not None
        assert catalog_support is not None
        # Bare ``cursor`` carries the built-in's default ``yolo_flag``
        # and ``output_flag`` but NO ``--model`` override (the
        # default Auto routing wins at runtime).
        assert config.model_flag is None
        assert config.yolo_flag == "--yolo"
        assert config.output_flag == "--output-format stream-json"
        assert config.print_flag == "--print"
        assert config.can_commit is True
