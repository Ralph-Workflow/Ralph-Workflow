"""Lock the development-result proof guidance contract (UNIT U-result).

The format doc ``ralph/mcp/artifacts/format_docs/development_result.md``
and the shared prompt partial
``ralph/prompts/templates/shared/_development_result_proof.jinja`` must
agree with the runtime behavior in ``ralph/phases/execution.py``:

- Plan-item proof IDs are shape-independent: on plan items the
  validator rejects only duplicate IDs, a missing or unknown
  ``Disposition``, a missing ``Rationale`` for
  ``adapted`` / ``not_applicable`` / ``blocked``, and ``blocked`` in a
  completed result (the artifact grammar gate, not proof validation).
- Analysis finding IDs are validated exactly: duplicate analysis-item
  proof entries and missing or unknown analysis finding IDs are hard
  errors raised by ``_analysis_proof_errors``.

The shipped documents' literal contents are the regression contract, so
the tests read them directly from the repo; a fixture copy would assert
against the fixture, not the shipped guidance.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FORMAT_DOC = _REPO_ROOT / "ralph" / "mcp" / "artifacts" / "format_docs" / "development_result.md"
_JINJA_PARTIAL = (
    _REPO_ROOT / "ralph" / "prompts" / "templates" / "shared" / "_development_result_proof.jinja"
)


def _normalized(path: Path) -> str:
    """Return the file's text with every whitespace run collapsed.

    The markdown source wraps long sentences across lines, so
    phrase-level assertions run against whitespace-normalized text.
    """
    return " ".join(path.read_text(encoding="utf-8").split())


def test_format_doc_drops_stale_plan_item_proof_gate_claims() -> None:
    """The stale proof-gate claims are gone: neither the "one item per
    usable extracted plan reference" requirement nor a "missing
    plan-item" hard error appears anywhere in the format doc."""
    text = _normalized(_FORMAT_DOC)

    assert "proof policy requires one item per usable extracted plan reference" not in text
    assert "missing plan-item" not in text


def test_format_doc_states_analysis_finding_ids_are_validated_exactly() -> None:
    """The format doc names what ``execution.py`` enforces at proof
    validation: duplicate analysis-item proof entries, and missing or
    unknown analysis finding IDs, validated exactly against the prior
    analysis's stable finding IDs."""
    text = _normalized(_FORMAT_DOC)

    assert "duplicate analysis-item proof entries" in text
    assert "missing or unknown analysis finding IDs" in text
    assert "analysis finding IDs are validated exactly" in text


def test_format_doc_scopes_plan_item_proof_to_actual_plan_references() -> None:
    """Plan-item proof asks for one item per plan reference the plan
    actually uses; coverage of the plan's intent is enforced through the
    development-analysis feedback loop, not by exact ID matching or a
    hard missing-entry error."""
    text = _normalized(_FORMAT_DOC)

    assert "one item per plan reference the plan actually uses" in text
    assert "not by exact ID matching or a hard missing-entry error" in text


def test_format_doc_partial_guidance_names_parallel_dispatch() -> None:
    """The partial-outcome guidance directs remaining independent slices
    to parallel dispatch rather than piecemeal partial handbacks."""
    text = _normalized(_FORMAT_DOC)

    assert "dispatch them in parallel" in text


def test_jinja_partial_scopes_shape_independence_to_plan_references() -> None:
    """The bare "only rejects duplicate IDs" understatement is gone: the
    shape-independence claim is scoped to ``## Plan Items Proven``, so
    the unscoped heading and the unscoped validator sentence no longer
    appear."""
    text = _normalized(_JINJA_PARTIAL)

    assert "**Proof IDs are shape-independent.**" not in text
    assert "## Plan Items Proven` are shape-independent.**" in text
    assert "solely for not matching a plan-parsed ID; it only rejects duplicate IDs" not in text


def test_jinja_partial_requires_exact_analysis_finding_id_match() -> None:
    """``## Analysis Items Addressed`` IDs must exactly match the prior
    analysis's stable finding IDs: a missing or unknown analysis finding
    ID fails proof validation."""
    text = _normalized(_JINJA_PARTIAL)

    assert "must EXACTLY match the prior analysis's stable finding ID" in text
    assert "missing or unknown analysis finding ID fails proof validation" in text
