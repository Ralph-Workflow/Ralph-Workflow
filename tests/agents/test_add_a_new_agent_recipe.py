
from __future__ import annotations


def test_recipe_docstring_references_canonical_doc() -> None:
    from pathlib import Path

    this_file = Path(__file__).resolve()
    text = this_file.read_text(encoding="utf-8")
    assert "adding-a-new-agent.md" in text, (
        "this test file must reference adding-a-new-agent.md so the "
        "executable recipe cannot drift from the documented one"
    )
