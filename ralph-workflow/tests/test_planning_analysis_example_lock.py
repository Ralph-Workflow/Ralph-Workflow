from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EXAMPLE = _REPO_ROOT / "ralph/mcp/artifacts/format_docs/examples/planning_analysis_decision.md"
_FORMAT_DOC = _REPO_ROOT / "ralph/mcp/artifacts/format_docs/planning_analysis_decision.md"
_TEMPLATE = _REPO_ROOT / "ralph/prompts/templates/planning_analysis.jinja"


def test_planning_analysis_examples_model_unit_properties() -> None:
    for path in (_EXAMPLE, _FORMAT_DOC):
        text = path.read_text(encoding="utf-8")
        assert "Paths:" in text or "Directories:" in text, path
        assert "focused check" in text, path


def test_planning_analysis_examples_drop_the_shared_contract_unit() -> None:
    texts = [
        _EXAMPLE.read_text(encoding="utf-8"),
        _FORMAT_DOC.read_text(encoding="utf-8"),
        _TEMPLATE.read_text(encoding="utf-8"),
    ]
    for text in texts:
        assert "shared-contract unit" not in text
