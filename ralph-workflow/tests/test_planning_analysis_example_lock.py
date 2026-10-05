"""Planning-analysis example lock (UNIT U-plan).

The shipped planning-analysis format doc and its standalone example
mirror must model what the review contract demands of a proposed unit
split: exact ``Paths:``/``Directories:`` ownership per unit, a per-unit
focused check, single-agent sizing, and no "shared-contract unit" (the
examples' own Evidence disowned it — "the cited paths do not consume
each other"). These files' literal contents are the regression
contract; a fixture copy would assert against the fixture, not the
shipped docs, so the locks would stop detecting drift.
"""

from __future__ import annotations

from pathlib import Path

from ralph.prompts.template_context import TemplateContext

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EXAMPLE = _REPO_ROOT / "ralph/mcp/artifacts/format_docs/examples/planning_analysis_decision.md"
_FORMAT_DOC = _REPO_ROOT / "ralph/mcp/artifacts/format_docs/planning_analysis_decision.md"


def test_planning_analysis_examples_model_unit_properties() -> None:
    """The standalone example and format doc name per-unit ownership and
    a per-unit focused check."""
    for path in (_EXAMPLE, _FORMAT_DOC):
        text = path.read_text(encoding="utf-8")
        assert "Paths:" in text or "Directories:" in text, path
        assert "focused check" in text, path


def test_planning_analysis_examples_drop_the_shared_contract_unit() -> None:
    """The "shared-contract unit" the examples used to propose is gone
    from the template source, the primary format doc, and the standalone
    example."""
    texts = [
        _EXAMPLE.read_text(encoding="utf-8"),
        _FORMAT_DOC.read_text(encoding="utf-8"),
        TemplateContext.default().registry.get_template("planning_analysis"),
    ]
    for text in texts:
        assert "shared-contract unit" not in text
