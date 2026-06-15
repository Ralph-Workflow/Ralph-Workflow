"""Markdown linter test for docs/agents/adding-a-new-agent.md."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


def test_doc_exists_and_non_empty() -> None:
    doc_path = Path("docs/agents/adding-a-new-agent.md")
    assert doc_path.exists(), "adding-a-new-agent.md must exist"
    assert doc_path.stat().st_size > 0, "adding-a-new-agent.md must be non-empty"


def test_workflow_sections_present_in_order() -> None:
    doc_content = Path("docs/agents/adding-a-new-agent.md").read_text(encoding="utf-8")

    sections = re.findall(r"^##\s+(.*)$", doc_content, re.MULTILINE)

    # Ensure our target workflow sections are present
    assert any("Add" in s for s in sections), "Add section must be present"
    assert any("Update" in s for s in sections), "Update section must be present"
    assert any("Remove" in s for s in sections), "Remove section must be present"

    # Check order: Add before Update before Remove
    add_idx = next(i for i, s in enumerate(sections) if "Add" in s)
    update_idx = next(i for i, s in enumerate(sections) if "Update" in s)
    remove_idx = next(i for i, s in enumerate(sections) if "Remove" in s)

    assert add_idx < update_idx < remove_idx, "Sections must be in order: Add -> Update -> Remove"

    # Split by headings to check each has a non-empty paragraph
    parts = re.split(r"^##\s+.*$", doc_content, flags=re.MULTILINE)
    # The first part is top matter; parts 1, 2, 3 correspond to the headings
    for part in parts[1:4]:
        paragraphs = [p.strip() for p in part.split("\n\n") if p.strip()]
        assert len(paragraphs) > 0, "Each section must contain at least one non-empty paragraph"


def test_examples_present_and_compilable() -> None:
    doc_content = Path("docs/agents/adding-a-new-agent.md").read_text(encoding="utf-8")

    # Find all python blocks
    blocks = re.findall(r"```python\n(.*?)\n```", doc_content, re.DOTALL)
    assert len(blocks) >= 2, "Must contain at least 2 python code examples"

    headless_ok = False
    interactive_ok = False

    for block in blocks:
        # Check compilability using ast.parse (replacing '...' placeholder
        # to make it valid python if used)
        code = (
            block.replace("...", "pass")
            .replace("new_spec", "None")
            .replace("new_parser", "None")
            .replace("new_strategy", "None")
            .replace("new_config", "None")
        )
        try:
            ast.parse(code)
        except SyntaxError as e:
            pytest.fail(f"Code example failed to compile:\n{block}\nError: {e}")

        # Check headless vs interactive signatures
        if "requires_pty=False" in block or "AgentTransport.GENERIC" in block:
            headless_ok = True
        if "requires_pty=True" in block or "AgentTransport.CLAUDE_INTERACTIVE" in block:
            interactive_ok = True

    assert headless_ok, "Must contain a headless agent example"
    assert interactive_ok, "Must contain an interactive agent example"


def test_backlink_from_architecture_doc() -> None:
    arch_path = Path("docs/agents/architecture.md")
    assert arch_path.exists()
    arch_content = arch_path.read_text(encoding="utf-8")

    # Must contain ## Adding a new agent heading
    assert "## Adding a new agent" in arch_content

    # Must contain a link to adding-a-new-agent.md in that section
    parts = arch_content.split("## Adding a new agent")
    assert len(parts) > 1
    section_content = parts[1]
    assert "adding-a-new-agent.md" in section_content, (
        "architecture.md must link to adding-a-new-agent.md in ## Adding a new agent section"
    )


def test_discoverability_link_from_contributing_doc() -> None:
    contrib_path = Path("CONTRIBUTING.md")
    assert contrib_path.exists()
    contrib_content = contrib_path.read_text(encoding="utf-8")

    # Must contain ## How to add a new agent heading
    assert "## How to add a new agent" in contrib_content

    # Must contain a link to adding-a-new-agent.md in that section
    parts = contrib_content.split("## How to add a new agent")
    assert len(parts) > 1
    section_content = parts[1]
    assert "adding-a-new-agent.md" in section_content, (
        "CONTRIBUTING.md must link to adding-a-new-agent.md in ## How to add a new agent section"
    )


def test_recipe_test_docstrings_point_to_doc() -> None:
    recipe1 = Path("tests/agents/test_add_a_new_agent_recipe.py")
    recipe2 = Path("tests/agents/test_add_a_new_interactive_agent_recipe.py")

    assert recipe1.exists()
    assert recipe2.exists()

    assert "adding-a-new-agent.md" in recipe1.read_text(encoding="utf-8"), (
        "test_add_a_new_agent_recipe.py must reference adding-a-new-agent.md"
    )
    assert "adding-a-new-agent.md" in recipe2.read_text(encoding="utf-8"), (
        "test_add_a_new_interactive_agent_recipe.py must reference adding-a-new-agent.md"
    )


def test_no_known_bad_api_references() -> None:
    doc_content = Path("docs/agents/adding-a-new-agent.md").read_text(encoding="utf-8")

    bad_strings = [
        "agent_registry.unregister",
        "registry.unregister",
        "the registry raises on duplicate",
        "registry.register raises",
        "agents.unregister",
    ]

    for bad in bad_strings:
        assert bad not in doc_content, f"adding-a-new-agent.md contains bad API reference: {bad}"
