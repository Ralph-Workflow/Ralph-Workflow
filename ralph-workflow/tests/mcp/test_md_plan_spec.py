"""Pure behavior tests for the JSON-free plan markdown grammar."""

from typing import cast

import pytest

from ralph.mcp.artifacts.markdown import MarkdownArtifactError, parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.markdown.specs import PLAN_SPEC
from ralph.mcp.artifacts.markdown.specs.plan import edit_plan_step_markdown


def _plan_document() -> str:
    return """---
type: plan
schema_version: 1
intent_verb: add
---
## Summary
Migrate the plan artifact to a JSON-free markdown grammar.

Intent: Plan documents are authored as plain markdown.
Coverage: feature, test

## Scope
- [SC-1] Redesign the plan grammar
  Category: feature
- [SC-2] Extend the closed parser with step blocks
  Category: feature
- [SC-3] Rewrite the plan spec tests
  Category: test
  Count: 1 file

## Skills MCP
Skills: test-driven-development
MCPs: docs-mcp-server

## Steps

### [S-1] Implement the markdown plan spec
Rewrite the mapping so labeled fields replace embedded JSON.

Type: file_change
Priority: high
Files:
- modify ralph/mcp/artifacts/markdown/specs/plan.py
- create tests/mcp/test_md_plan_spec.py
Satisfies: AC-01
Rationale: The plan is the most important artifact.
Evidence:
- file: ralph/mcp/artifacts/markdown/specs/plan.py

### [S-2] Verify the focused suites
Run the markdown artifact suites.

Type: verify
Depends on: S-1
Verify: pytest tests/mcp/test_md_plan_spec.py -q

## Critical Files
- [CF-1] ralph/mcp/artifacts/markdown/specs/plan.py
  Action: modify
  Changes: rewrite the document mapping
- [CF-2] ralph/mcp/artifacts/plan/_validation.py
  Purpose: canonical validation parity gate

## Constraints
Must not break:
- existing markdown artifact specs
Performance budget: focused suites stay under one second

## Design
Grammar decisions and notes live here as prose.

Outcome: Plan documents contain no embedded JSON.
Non-goals:
- redesigning what plans say

## Acceptance Criteria
- [AC-01] The plan grammar contains no JSON anywhere
  Satisfied by: S-1
  Verify: pytest tests/mcp/test_md_plan_spec.py -q

## Risks
- [R-1] Validation drift between markdown and the canonical model
  Severity: medium
  Mitigation: Reuse the canonical plan normalizer on the mapped content.

## Verification
- [V-1] pytest tests/mcp/test_md_plan_spec.py -q
  Expect: focused tests pass
  Timeout: 120
"""


def _steps(content: dict[str, object]) -> list[dict[str, object]]:
    steps = content["steps"]
    assert isinstance(steps, list)
    return [cast("dict[str, object]", step) for step in steps]


def test_plan_document_maps_to_canonical_content_without_json() -> None:
    content, diagnostics = parse_and_validate(_plan_document(), PLAN_SPEC)

    assert diagnostics == []
    assert get_spec("plan") is PLAN_SPEC
    assert content["schema_version"] == 1

    summary = cast("dict[str, object]", content["summary"])
    assert summary["intent_verb"] == "add"
    assert summary["intent"] == "Plan documents are authored as plain markdown."
    assert summary["context"] == "Migrate the plan artifact to a JSON-free markdown grammar."
    assert summary["coverage_areas"] == ["feature", "test"]
    scope_items = cast("list[dict[str, object]]", summary["scope_items"])
    assert scope_items[0] == {"text": "Redesign the plan grammar", "category": "feature"}
    assert scope_items[2]["count"] == "1 file"

    skills = cast("dict[str, object]", content["skills_mcp"])
    assert skills == {"skills": ["test-driven-development"], "mcps": ["docs-mcp-server"]}

    steps = _steps(content)
    assert steps[0]["number"] == 1
    assert steps[0]["title"] == "Implement the markdown plan spec"
    assert steps[0]["content"] == "Rewrite the mapping so labeled fields replace embedded JSON."
    assert steps[0]["step_type"] == "file_change"
    assert steps[0]["priority"] == "high"
    assert steps[0]["targets"] == [
        {"path": "ralph/mcp/artifacts/markdown/specs/plan.py", "action": "modify"},
        {"path": "tests/mcp/test_md_plan_spec.py", "action": "create"},
    ]
    assert steps[0]["satisfies"] == ["AC-01"]
    assert steps[0]["expected_evidence"] == [
        {"kind": "file", "ref": "ralph/mcp/artifacts/markdown/specs/plan.py"}
    ]
    assert steps[1]["depends_on"] == [1]
    assert steps[1]["verify_command"] == "pytest tests/mcp/test_md_plan_spec.py -q"

    critical = cast("dict[str, object]", content["critical_files"])
    assert critical["primary_files"] == [
        {
            "path": "ralph/mcp/artifacts/markdown/specs/plan.py",
            "action": "modify",
            "estimated_changes": "rewrite the document mapping",
        }
    ]
    assert critical["reference_files"] == [
        {
            "path": "ralph/mcp/artifacts/plan/_validation.py",
            "purpose": "canonical validation parity gate",
        }
    ]

    constraints = cast("dict[str, object]", content["constraints"])
    assert constraints["must_not_break"] == ["existing markdown artifact specs"]
    assert constraints["performance_budget"] == "focused suites stay under one second"

    design = cast("dict[str, object]", content["design"])
    assert design["outcome"] == "Plan documents contain no embedded JSON."
    assert design["notes"] == "Grammar decisions and notes live here as prose."
    assert design["non_goals"] == {"items": ["redesigning what plans say"]}
    acceptance = cast("dict[str, object]", design["acceptance_criteria"])
    criteria = cast("list[dict[str, object]]", acceptance["criteria"])
    assert criteria[0]["id"] == "AC-01"
    assert criteria[0]["satisfied_by_steps"] == [1]
    assert criteria[0]["verification_step"] == "pytest tests/mcp/test_md_plan_spec.py -q"

    risks = cast("list[dict[str, object]]", content["risks_mitigations"])
    assert risks[0]["severity"] == "medium"
    assert risks[0]["mitigation"] == "Reuse the canonical plan normalizer on the mapped content."

    verification = cast("list[dict[str, object]]", content["verification_strategy"])
    assert verification[0] == {
        "method": "pytest tests/mcp/test_md_plan_spec.py -q",
        "expected_outcome": "focused tests pass",
        "timeout_seconds": 120,
    }


def test_plan_spec_warns_and_coerces_non_security_vocabulary() -> None:
    document = (
        _plan_document()
        .replace("intent_verb: add", "intent_verb: invented")
        .replace("Category: feature\n- [SC-2]", "Category: invented\n- [SC-2]")
        .replace("Type: file_change", "Type: invented")
        .replace("- modify ralph/mcp", "- invented ralph/mcp")
        .replace("Severity: medium", "Severity: invented")
        .replace("- file: ralph", "- invented: ralph")
        .replace("Coverage: feature, test", "Coverage: feature, invented")
    )

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert all(diagnostic.severity == "warning" for diagnostic in diagnostics)
    assert {diagnostic.rule_id for diagnostic in diagnostics} == {
        "SPEC009",
        "PLAN002",
        "PLAN003",
        "PLAN005",
        "PLAN006",
        "PLAN007",
        "PLAN008",
    }
    summary = cast("dict[str, object]", content["summary"])
    assert summary["intent_verb"] == "add"
    assert summary["coverage_areas"] == ["feature"]
    scope_items = cast("list[dict[str, object]]", summary["scope_items"])
    assert scope_items[0]["category"] == "other"
    steps = _steps(content)
    assert "step_type" not in steps[0] or steps[0]["step_type"] == "action"
    first_target = cast("list[dict[str, object]]", steps[0]["targets"])[0]
    assert first_target["action"] == "modify"
    assert first_target["path"] == "ralph/mcp/artifacts/markdown/specs/plan.py"


def test_unknown_field_label_in_step_is_prose_with_warning() -> None:
    document = _plan_document().replace(
        "Rationale: The plan is the most important artifact.",
        "Caveat: This line is prose, not a grammar field.",
    )

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert [diagnostic.rule_id for diagnostic in diagnostics] == ["PLAN009"]
    assert diagnostics[0].severity == "warning"
    steps = _steps(content)
    step_content = steps[0]["content"]
    assert isinstance(step_content, str)
    assert "Caveat: This line is prose, not a grammar field." in step_content


def test_dangling_step_and_criterion_references_are_line_anchored_errors() -> None:
    document = _plan_document().replace("Depends on: S-1", "Depends on: S-9").replace(
        "Satisfied by: S-1", "Satisfied by: S-8"
    )

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert content == {}
    errors = [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    assert {diagnostic.rule_id for diagnostic in errors} == {"PLAN021"}
    assert all(diagnostic.line > 1 for diagnostic in errors)
    assert {diagnostic.section for diagnostic in errors} == {"Steps", "Acceptance Criteria"}


def test_dependency_cycles_are_rejected() -> None:
    document = _plan_document().replace(
        "Type: file_change",
        "Type: file_change\nDepends on: S-2",
    )

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert content == {}
    assert any(
        "cycle" in diagnostic.message and diagnostic.severity == "error"
        for diagnostic in diagnostics
    )


def test_step_type_contracts_hard_fail_with_step_anchored_diagnostics() -> None:
    no_files = _plan_document().replace(
        "Files:\n- modify ralph/mcp/artifacts/markdown/specs/plan.py\n"
        "- create tests/mcp/test_md_plan_spec.py\n",
        "",
    )
    no_verify = _plan_document().replace(
        "Verify: pytest tests/mcp/test_md_plan_spec.py -q\n\n## Critical Files",
        "\n## Critical Files",
    )

    _, files_diagnostics = parse_and_validate(no_files, PLAN_SPEC)
    _, verify_diagnostics = parse_and_validate(no_verify, PLAN_SPEC)

    assert any(
        diagnostic.rule_id == "PLAN010" and diagnostic.section == "Steps" and "S-1" in diagnostic.message
        for diagnostic in files_diagnostics
    )
    assert any(
        diagnostic.rule_id == "PLAN011" and diagnostic.section == "Steps" and "S-2" in diagnostic.message
        for diagnostic in verify_diagnostics
    )


def test_required_item_fields_are_enforced_at_the_item_line() -> None:
    document = _plan_document().replace(
        "  Mitigation: Reuse the canonical plan normalizer on the mapped content.\n", ""
    ).replace("  Expect: focused tests pass\n", "")

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert content == {}
    errors = [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    assert {diagnostic.rule_id for diagnostic in errors} == {"PLAN020"}
    assert {diagnostic.section for diagnostic in errors} == {"Risks", "Verification"}
    assert any("Mitigation" in diagnostic.message for diagnostic in errors)
    assert any("Expect" in diagnostic.message for diagnostic in errors)


def test_malformed_and_duplicate_step_ids_are_rejected() -> None:
    duplicate = _plan_document().replace("### [S-2] Verify the focused suites", "### [S-1] Verify the focused suites")
    malformed = _plan_document().replace("### [S-2]", "### [STEP-2]")

    _, duplicate_diagnostics = parse_and_validate(duplicate, PLAN_SPEC)
    _, malformed_diagnostics = parse_and_validate(malformed, PLAN_SPEC)

    assert any(diagnostic.rule_id == "REF002" for diagnostic in duplicate_diagnostics)
    assert any(
        diagnostic.rule_id == "PLAN022" and "STEP-2" in diagnostic.message
        for diagnostic in malformed_diagnostics
    )


def test_shell_invocation_guard_still_hard_fails() -> None:
    document = _plan_document().replace(
        "- [V-1] pytest tests/mcp/test_md_plan_spec.py -q",
        "- [V-1] bash -c 'pytest tests'",
    )

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert content == {}
    assert any(
        "shell interpreter" in diagnostic.message and diagnostic.severity == "error"
        for diagnostic in diagnostics
    )


def test_truncated_document_degrades_to_line_anchored_diagnostics() -> None:
    truncated = _plan_document().split("Satisfies: AC-01")[0]

    content, diagnostics = parse_and_validate(truncated, PLAN_SPEC)

    assert content == {}
    errors = [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    assert errors
    missing_sections = {
        diagnostic.section for diagnostic in errors if diagnostic.rule_id == "SPEC008"
    }
    assert {"Risks", "Verification", "Critical Files"} <= missing_sections


def test_replacing_a_step_with_its_own_block_round_trips_identically() -> None:
    document = _plan_document()
    block = (
        "### [S-2] Verify the focused suites\n"
        "Run the markdown artifact suites.\n"
        "\n"
        "Type: verify\n"
        "Depends on: S-1\n"
        "Verify: pytest tests/mcp/test_md_plan_spec.py -q\n"
    )

    edited = edit_plan_step_markdown(document, "replace", "S-2", block)

    original_content, original_diagnostics = parse_and_validate(document, PLAN_SPEC)
    edited_content, edited_diagnostics = parse_and_validate(edited, PLAN_SPEC)
    assert original_diagnostics == []
    assert edited_diagnostics == []
    assert edited_content == original_content
    assert edited == document


def test_inserting_and_moving_steps_keeps_ids_and_references_stable() -> None:
    inserted = edit_plan_step_markdown(
        _plan_document(),
        "insert",
        "S-3",
        "### [S-3] Document the grammar\nSummarize the grammar for reconciliation.\n\nType: action\nDepends on: S-2\n",
    )
    moved = edit_plan_step_markdown(inserted, "move", "S-3", index=1)

    content, diagnostics = parse_and_validate(moved, PLAN_SPEC)

    assert diagnostics == []
    steps = _steps(content)
    assert [step["number"] for step in steps] == [3, 1, 2]
    assert steps[0]["depends_on"] == [2]
    assert steps[2]["depends_on"] == [1]


def test_removing_a_referenced_step_is_rejected_and_leaves_input_valid() -> None:
    with pytest.raises(MarkdownArtifactError) as excinfo:
        edit_plan_step_markdown(_plan_document(), "remove", "S-1")

    assert any(
        diagnostic.rule_id == "PLAN021" for diagnostic in excinfo.value.diagnostics
    )


def test_edit_rejects_replacement_that_is_not_a_single_matching_block() -> None:
    with pytest.raises(ValueError, match="step_id"):
        edit_plan_step_markdown(
            _plan_document(),
            "replace",
            "S-2",
            "### [S-9] Wrong identifier\nBody.\n\nType: action\n",
        )
    with pytest.raises(ValueError, match="block"):
        edit_plan_step_markdown(_plan_document(), "replace", "S-2", "just prose, no heading\n")
