"""Regression coverage for relaxed, free-shape Markdown plans."""

from __future__ import annotations

from typing import cast

import pytest

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs import PLAN_SPEC
from ralph.mcp.artifacts.markdown.specs.plan import edit_plan_step_markdown


def _single_step_document(*, extra: str = "", section: str = "Implementation") -> str:
    return f"""---
type: plan
---
## {section}

### [S-1] Implement the change
Apply the requested behavior.

Type: action
{extra}"""


def _editable_free_shape_document() -> str:
    return """---
type: plan
---
## Alpha Subplan

### [S-1] First
Implement the first part.

Type: action

### [S-2] Second
Implement the second part.

Type: action

## Alpha Subplan

### [S-3] Third
Implement the third part.

Type: action
"""


def _five_nested_work_units_document() -> str:
    sections = []
    for number, name in enumerate(("api", "web", "docs", "contract", "integration"), start=1):
        sections.append(
            f"""## Work Units
- [{name}] Implement the {name} unit
  Directories: src/{name}

### [S-{number}] Implement {name}
Change the {name} component.

Type: file_change
Files:
- modify src/{name}/main.py
"""
        )
    return "---\ntype: plan\n---\n" + "\n".join(sections)


@pytest.mark.parametrize("section", ["Subplan Alpha", "Work Units"])
@pytest.mark.parametrize("bad_id", ["STEP-2", "S-0", "S-01", "s-2"])
def test_plan_grammar_regression_malformed_step_like_ids_fail_document_wide(
    section: str, bad_id: str
) -> None:
    """Regression for plan blocker 1: typo-like step IDs must never disappear."""
    document = (
        _single_step_document()
        + f"""

## {section}

### [{bad_id}] Mistyped step
This heading is intended to be a plan step.

Type: action
"""
    )

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert content == {}
    assert any(
        diagnostic.rule_id == "PLAN022"
        and bad_id in diagnostic.message
        and diagnostic.severity == "error"
        for diagnostic in diagnostics
    )


@pytest.mark.parametrize(
    "section",
    ["Subplan: Alpha / API", "Équipe — données", "検証・API (第2期)"],
)
def test_plan_grammar_regression_punctuated_unicode_h2_titles_are_safe(
    section: str,
) -> None:
    """Regression for plan blocker 5: meaningful Unicode H2 names remain parseable."""
    content, diagnostics = parse_and_validate(
        _single_step_document(section=section), PLAN_SPEC
    )

    assert diagnostics == []
    steps = cast("list[dict[str, object]]", content["steps"])
    assert steps[0]["title"] == "Implement the change"


def test_plan_grammar_regression_ac_items_are_discovered_outside_named_section() -> None:
    """Regression for plan blocker 3: unambiguous AC items are document-wide."""
    document = _single_step_document(extra="Satisfies: AC-01\n") + """

## Product Outcomes
- [AC-01] The focused suite proves the behavior
  Verify: pytest tests/mcp/test_md_plan_relaxation.py -q
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    design = cast("dict[str, object]", content["design"])
    acceptance = cast("dict[str, object]", design["acceptance_criteria"])
    criteria = cast("list[dict[str, object]]", acceptance["criteria"])
    assert criteria == [
        {
            "id": "AC-01",
            "description": "The focused suite proves the behavior",
            "verification_step": "pytest tests/mcp/test_md_plan_relaxation.py -q",
        }
    ]


@pytest.mark.parametrize(
    ("criterion_field", "verification_expect"),
    [
        ("Verify: code is clean", "exit code 0"),
        ("Evidence: clean", "exit code 0"),
        (
            "Verify: pytest tests/mcp/test_md_plan_relaxation.py -q",
            "looks good",
        ),
    ],
    ids=["vague-command", "vague-evidence", "vague-outcome"],
)
def test_plan_grammar_regression_evaluatable_claims_must_be_concrete(
    criterion_field: str, verification_expect: str
) -> None:
    """Regression for plan blocker 4: subjective proof text is not evaluatable."""
    document = _single_step_document() + f"""

## Acceptance Criteria
- [AC-01] The behavior is proven
  {criterion_field}

## Verification
- [V-1] pytest tests/mcp/test_md_plan_relaxation.py -q
  Expect: {verification_expect}
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert content == {}
    assert any(
        diagnostic.rule_id == "PLAN020"
        and "concrete" in diagnostic.message
        and diagnostic.severity == "error"
        for diagnostic in diagnostics
    )


def test_plan_grammar_regression_concrete_commands_and_artifacts_remain_valid() -> None:
    """Positive coverage for plan blocker 4's command and artifact proof paths."""
    document = _single_step_document() + """

## Product Outcomes
- [AC-01] The command proves the behavior
  Verify: pytest tests/mcp/test_md_plan_relaxation.py -q
- [AC-02] The generated report proves the behavior
  Evidence: reports/plan-relaxation.json

## Verification
- [V-1] pytest tests/mcp/test_md_plan_relaxation.py -q
  Expect: exit code 0 and all focused tests pass
- [V-2] Inspect reports/plan-relaxation.json
  Expect: the status field equals completed
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    assert len(cast("list[object]", content["verification_strategy"])) == 2


def test_plan_editor_regression_replace_resolves_across_repeated_free_sections() -> None:
    """Regression for plan blocker 2: replacement uses the global step namespace."""
    replacement = """### [S-3] Third, revised
Implement the revised third part.

Type: action"""

    edited = edit_plan_step_markdown(
        _editable_free_shape_document(),
        "replace",
        "S-3",
        replacement,
        None,
    )

    assert "### [S-3] Third, revised" in edited
    assert "### [S-1] First" in edited
    assert edited.count("## Alpha Subplan") == 2


def test_plan_editor_regression_move_resolves_across_repeated_free_sections() -> None:
    """Regression for plan blocker 2: movement uses document-wide source order."""
    edited = edit_plan_step_markdown(
        _editable_free_shape_document(), "move", "S-3", None, 1
    )

    assert edited.index("### [S-3]") < edited.index("### [S-1]") < edited.index("### [S-2]")
    assert edited.count("## Alpha Subplan") == 2


def test_plan_editor_regression_remove_resolves_across_repeated_free_sections() -> None:
    """Regression for plan blocker 2: removal finds globally nested step blocks."""
    edited = edit_plan_step_markdown(
        _editable_free_shape_document(), "remove", "S-2", None, None
    )

    assert "### [S-2]" not in edited
    assert "### [S-1]" in edited
    assert "### [S-3]" in edited


def test_plan_editor_regression_insert_resolves_across_repeated_free_sections() -> None:
    """Regression for plan blocker 2: insertion honors the global step position."""
    replacement = """### [S-4] Inserted
Implement the inserted part.

Type: action"""

    edited = edit_plan_step_markdown(
        _editable_free_shape_document(), "insert", "S-4", replacement, 3
    )

    assert edited.index("### [S-2]") < edited.index("### [S-4]") < edited.index("### [S-3]")
    assert edited.count("## Alpha Subplan") == 2


def test_plan_grammar_regression_repeated_work_units_retain_nested_step_ownership() -> None:
    """Regression for plan blocker 6: normalized units retain nested mini-plan IDs."""
    content, diagnostics = parse_and_validate(_five_nested_work_units_document(), PLAN_SPEC)

    assert diagnostics == []
    work_units = cast("list[dict[str, object]]", content["work_units"])
    assert [unit["step_ids"] for unit in work_units] == [
        ["S-1"],
        ["S-2"],
        ["S-3"],
        ["S-4"],
        ["S-5"],
    ]
