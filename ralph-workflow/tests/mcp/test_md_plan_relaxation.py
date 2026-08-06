"""Regression coverage for descriptive plan structure around strict executor steps."""

from __future__ import annotations

import pytest

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs import PLAN_SPEC
from tests._support.typed_accessors import must_dict_list, must_mapping


def _step(number: int, title: str, *, path: str = "src/example.py", extra: str = "") -> str:
    return f"""### [S-{number}] {title}
Implement the scoped behavior in the named target.

Type: file_change
Files:
- modify {path}
Verify: pytest tests/mcp/test_md_plan_relaxation.py -q
Expect: the focused plan-relaxation tests pass with exit code 0
{extra}"""


def _document(*, section: str = "Implementation", extra: str = "") -> str:
    return f"""---
type: plan
---
## {section}

{_step(1, "Implement the change")}
{extra}"""


@pytest.mark.parametrize("section", ["Subplan: Alpha / API", "Équipe — données", "検証・API (第2期)"])
def test_plan_grammar_regression_punctuated_unicode_h2_titles_are_safe(section: str) -> None:
    """Executor-ready steps remain valid below arbitrary descriptive headings."""
    content, diagnostics = parse_and_validate(_document(section=section), PLAN_SPEC)

    assert diagnostics == []
    assert must_dict_list(content["steps"])[0]["title"] == "Implement the change"


def test_plan_grammar_regression_ac_items_are_discovered_outside_named_section() -> None:
    """An AC item maps document-wide without weakening its owning step."""
    document = _document(
        extra="""
## Product Outcomes
- [AC-01] The focused suite proves the behavior
  Verify: pytest tests/mcp/test_md_plan_relaxation.py -q
  Expect: the focused plan-relaxation tests pass with exit code 0
"""
    )

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    criteria = must_dict_list(must_mapping(content["design"])["acceptance_criteria"]["criteria"])
    assert criteria[0]["id"] == "AC-01"


def test_plan_grammar_discovers_criterion_after_nested_step() -> None:
    """A criterion after a strict step remains a section item, not step prose."""
    document = _document(
        section="API Subplan",
        extra="""
- [AC-01] The API contract is proven
  Verify: pytest tests/api/test_contract.py -q
  Expect: the focused API contract tests pass with exit code 0
""",
    )

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    assert must_dict_list(content["steps"])[0]["number"] == 1
    assert must_dict_list(must_mapping(content["design"])["acceptance_criteria"]["criteria"])[0]["id"] == "AC-01"


def test_custom_fan_out_lookalike_remains_descriptive() -> None:
    """Only exact fan-out headings opt into work-unit parsing."""
    content, diagnostics = parse_and_validate(
        _document(extra="\n## work units\n- api: ordinary descriptive prose.\n"), PLAN_SPEC
    )

    assert diagnostics == []
    assert "work_units" not in content


def test_acceptance_criterion_inside_work_units_is_not_a_phantom_unit() -> None:
    """Criterion IDs retain criterion semantics inside a valid work unit."""
    document = f"""---
type: plan
---
## Work Units
- [api] Implement the API
  Directories: src/api
- [AC-01] The API report proves completion
  Evidence: reports/api-proof.json

{_step(1, "Implement the API", path="src/api/routes.py")}
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    assert [unit["unit_id"] for unit in must_dict_list(content["work_units"])] == ["api"]
    assert must_dict_list(must_mapping(content["design"])["acceptance_criteria"]["criteria"])[0]["id"] == "AC-01"


def test_plan_grammar_regression_vague_document_wide_proof_is_advisory() -> None:
    """Descriptive global proof remains advisory while step evidence is strict."""
    content, diagnostics = parse_and_validate(
        _document(extra="\n## Proof Matrix\n- [V-1] check it manually\n  Expect: everything works\n"),
        PLAN_SPEC,
    )

    assert content != {}
    assert any(item.rule_id == "PLAN020" and item.severity == "warning" for item in diagnostics)


def test_work_unit_owns_strict_nested_step() -> None:
    """A work unit retains its following executor-ready step ID."""
    document = f"""---
type: plan
---
## Work Units
- [api] Implement the API slice
  Directories: src/api

### Authentication
{_step(1, "Implement authentication", path="src/api/auth.py")}
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    assert must_dict_list(content["work_units"])[0]["step_ids"] == ["S-1"]


def test_malformed_step_id_blocks_submission_even_with_a_valid_step() -> None:
    """Malformed IDs cannot silently bypass development-result proof matching."""
    document = _document(
        extra="""
## Verification

### [S-01] Mistyped step
Type: verify
Verify: pytest tests/mcp/test_md_plan_relaxation.py -q
Expect: the focused plan-relaxation tests pass with exit code 0
"""
    )

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert content == {}
    assert any(item.rule_id == "PLAN022" and item.severity == "error" for item in diagnostics)
