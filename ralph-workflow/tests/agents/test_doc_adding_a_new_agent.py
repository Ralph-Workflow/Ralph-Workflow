"""Markdown linter test for docs/agents/adding-a-new-agent.md."""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from ralph.agents.registration import register_agent_support


def test_doc_exists_and_non_empty() -> None:
    doc_path = Path("docs/agents/adding-a-new-agent.md")
    assert doc_path.exists(), "adding-a-new-agent.md must exist"
    assert doc_path.stat().st_size > 0, "adding-a-new-agent.md must be non-empty"


def test_workflow_sections_present_in_order() -> None:
    doc_content = Path("docs/agents/adding-a-new-agent.md").read_text(encoding="utf-8")

    sections = re.findall(r"^##\s+(.*)$", doc_content, re.MULTILINE)

    assert any("Add" in s for s in sections), "Add section must be present"
    assert any("Update" in s for s in sections), "Update section must be present"
    assert any("Remove" in s for s in sections), "Remove section must be present"

    add_idx = next(i for i, s in enumerate(sections) if "Add" in s)
    update_idx = next(i for i, s in enumerate(sections) if "Update" in s)
    remove_idx = next(i for i, s in enumerate(sections) if "Remove" in s)

    assert add_idx < update_idx < remove_idx, "Sections must be in order: Add -> Update -> Remove"

    parts = re.split(r"^##\s+.*$", doc_content, flags=re.MULTILINE)
    for part in parts[1:4]:
        paragraphs = [p.strip() for p in part.split("\n\n") if p.strip()]
        assert len(paragraphs) > 0, "Each section must contain at least one non-empty paragraph"


def test_examples_present_and_compilable() -> None:
    doc_content = Path("docs/agents/adding-a-new-agent.md").read_text(encoding="utf-8")

    blocks = re.findall(r"```python\n(.*?)\n```", doc_content, re.DOTALL)
    assert len(blocks) >= 2, "Must contain at least 2 python code examples"

    headless_ok = False
    interactive_ok = False

    for block in blocks:
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

        if "requires_pty=False" in block or "AgentTransport.GENERIC" in block:
            headless_ok = True
        if "requires_pty=True" in block or "AgentTransport.CLAUDE_INTERACTIVE" in block:
            interactive_ok = True

    assert headless_ok, "Must contain a headless agent example"
    assert interactive_ok, "Must contain an interactive agent example"


def test_register_agent_support_examples_use_valid_kwargs() -> None:
    """Assert register_agent_support examples use real kwargs, not unsupported spec= or config=."""
    sig = inspect.signature(register_agent_support)
    valid_kwargs = set(sig.parameters.keys())

    doc_content = Path("docs/agents/adding-a-new-agent.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```python\n(.*?)\n```", doc_content, re.DOTALL)

    for block in blocks:
        if "register_agent_support" not in block:
            continue

        unsupported = ["spec=", "config="]
        for bad in unsupported:
            if bad in block:
                pytest.fail(
                    f"register_agent_support example contains unsupported kwarg '{bad}'. "
                    f"Valid kwargs are: {sorted(valid_kwargs)}"
                )


def test_backlink_from_architecture_doc() -> None:
    arch_path = Path("docs/agents/architecture.md")
    assert arch_path.exists()
    arch_content = arch_path.read_text(encoding="utf-8")

    assert "## Adding a new agent" in arch_content

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

    assert "## How to add a new agent" in contrib_content

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


def test_no_machine_local_file_links() -> None:
    doc_content = Path("docs/agents/adding-a-new-agent.md").read_text(encoding="utf-8")
    assert "file://" not in doc_content, (
        "adding-a-new-agent.md must not contain machine-local file:// links. "
        "Use relative repository paths or plain code references instead."
    )


def test_examples_have_required_imports() -> None:
    doc_content = Path("docs/agents/adding-a-new-agent.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```python\n(.*?)\n```", doc_content, re.DOTALL)

    assert len(blocks) >= 2, "Must contain at least 2 python code examples"

    register_blocks = [b for b in blocks if "register_agent_support" in b]
    assert len(register_blocks) >= 2, (
        "Must contain at least 2 register_agent_support examples"
    )

    for block in register_blocks:
        assert "AgentRegistry" in block or "registry" in block, (
            f"Example must import or reference AgentRegistry. "
            f"Block content:\n{block[:200]}"
        )
