"""Tests for docs/agents/architecture.md.

These tests verify the architecture doc describes the full agent support stack
and maintains add/update/remove discoverability.
"""

from __future__ import annotations

from pathlib import Path

DOC_PATH = Path("docs/agents/architecture.md")


class TestArchitectureDoc:
    """Tests for docs/agents/architecture.md."""

    def test_architecture_doc_mentions_all_six_layers(self) -> None:
        """Assert each of the 6 agent support layers appears in the doc."""
        content = DOC_PATH.read_text(encoding="utf-8")
        layers = [
            "registration",
            "parser",
            "strategy",
            "CommandBuilder",
            "RuntimeResolver",
            "config",
        ]
        for layer in layers:
            assert layer in content, f"architecture.md must mention the '{layer}' layer"

    def test_architecture_doc_links_to_adding_a_new_agent(self) -> None:
        """Assert the ## Adding a new agent section links to adding-a-new-agent.md."""
        content = DOC_PATH.read_text(encoding="utf-8")
        assert "adding-a-new-agent.md" in content, (
            "architecture.md must link to adding-a-new-agent.md "
            "from its ## Adding a new agent section"
        )

    def test_smoke_artifact_uses_canonical_markdown_path(self) -> None:
        content = DOC_PATH.read_text(encoding="utf-8")

        assert ".agent/artifacts/smoke_test_result.md" in content
        assert ".agent/artifacts/smoke_test_result.json" not in content

    def test_architecture_doc_does_not_describe_a_third_add_path(self) -> None:
        """Assert the doc does not describe COMMAND_BUILDERS[...] as a way to add an agent.

        That is a swap/intercept pattern, not an add path. The canonical add
        path is register_agent_support() -> AgentCatalog.add().
        """
        content = DOC_PATH.read_text(encoding="utf-8")
        bad_patterns = [
            "COMMAND_BUILDERS[",
            "RUNTIME_RESOLVERS[",
        ]
        for bad in bad_patterns:
            if bad in content:
                context_start = max(0, content.index(bad) - 80)
                context_end = min(len(content), content.index(bad) + 80)
                context = content[context_start:context_end]
                assert "adding a new agent" not in context.lower(), (
                    f"architecture.md must not describe {bad}... as the way to "
                    f"'add a new agent'. Found: ...{context}..."
                )
