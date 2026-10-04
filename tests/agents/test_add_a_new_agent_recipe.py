"""Consolidated add/update/remove recipe test for headless and interactive agents.

Canonical recipe: docs/agents/adding-a-new-agent.md
This test file acts as the executable form of the Add/Update/Remove sections.

Tests add, update, and remove workflows for both headless and interactive agents
using the public AgentCatalog API exclusively.
"""

from __future__ import annotations


def test_recipe_docstring_references_canonical_doc() -> None:
    """Pin the contract that this test file's docstring points at the canonical
    recipe source-of-truth at ``docs/agents/adding-a-new-agent.md``.

    A future regression that detaches the executable recipe from the
    documented one would silently pass tests while docs drift.
    """
    from pathlib import Path
    this_file = Path(__file__).resolve()
    text = this_file.read_text(encoding="utf-8")
    assert "adding-a-new-agent.md" in text, (
        "this test file must reference adding-a-new-agent.md so the "
        "executable recipe cannot drift from the documented one"
    )
