"""Pure behavior tests for the JSON-free plan markdown grammar."""

import pytest

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.markdown.specs import PLAN_SPEC
from tests._support.typed_accessors import (
    must_dict_list,
    must_mapping,
)


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
Expect: the focused markdown-plan tests pass with exit code 0

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
  Expect: the focused markdown-plan tests pass with exit code 0

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
    return [must_mapping(step) for step in steps]


def test_explicit_noop_plan_uses_minimal_closed_frontmatter_grammar() -> None:
    document = """---
type: plan
noop: true
---
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert content == {"noop": True}
    assert diagnostics == []


@pytest.mark.parametrize(
    "document",
    [
        "---\ntype: evil\nnoop: true\n---\n",
        "---\ntype: plan\nnoop: true\nintent_verb: add\n---\n",
        "---\ntype: plan\nnoop: true\n---\n## Summary\nDiscarded content.\n",
    ],
    ids=["wrong-type", "extra-metadata", "attached-section"],
)
def test_noop_plan_rejects_every_non_minimal_document(document: str) -> None:
    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert content == {}
    assert any(
        diagnostic.rule_id == "PLAN023"
        and "exactly 'type: plan' and 'noop: true' with no sections" in diagnostic.message
        for diagnostic in diagnostics
    )


@pytest.mark.parametrize("value", ["false", "yes", "1"])
def test_non_true_noop_value_cannot_bypass_ordinary_plan_sections(value: str) -> None:
    document = f"""---
type: plan
noop: {value}
---
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert content == {}
    assert any(
        diagnostic.rule_id == "PLAN023"
        and diagnostic.line == 3
        and "must be the literal value 'true'" in diagnostic.message
        for diagnostic in diagnostics
    )


def test_plan_document_maps_to_canonical_content_without_json() -> None:
    content, diagnostics = parse_and_validate(_plan_document(), PLAN_SPEC)

    assert diagnostics == []
    assert get_spec("plan") is PLAN_SPEC
    assert content["schema_version"] == 1

    summary = must_mapping(content["summary"])
    assert summary["intent_verb"] == "add"
    assert summary["intent"] == "Plan documents are authored as plain markdown."
    assert summary["context"] == "Migrate the plan artifact to a JSON-free markdown grammar."
    assert summary["coverage_areas"] == ["feature", "test"]
    scope_items = must_dict_list(summary["scope_items"])
    assert scope_items[0] == {"text": "Redesign the plan grammar", "category": "feature"}
    assert scope_items[2]["count"] == "1 file"

    skills = must_mapping(content["skills_mcp"])
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
    assert steps[1]["expected_outcome"] == ("the focused markdown-plan tests pass with exit code 0")

    critical = must_mapping(content["critical_files"])
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

    constraints = must_mapping(content["constraints"])
    assert constraints["must_not_break"] == ["existing markdown artifact specs"]
    assert constraints["performance_budget"] == "focused suites stay under one second"

    design = must_mapping(content["design"])
    assert design["outcome"] == "Plan documents contain no embedded JSON."
    assert design["notes"] == "Grammar decisions and notes live here as prose."
    assert design["non_goals"] == {"items": ["redesigning what plans say"]}
    acceptance = must_mapping(design["acceptance_criteria"])
    criteria = must_dict_list(acceptance["criteria"])
    assert criteria[0]["id"] == "AC-01"
    assert criteria[0]["satisfied_by_steps"] == [1]
    assert criteria[0]["verification_step"] == "pytest tests/mcp/test_md_plan_spec.py -q"
    assert criteria[0]["expected_outcome"] == (
        "the focused markdown-plan tests pass with exit code 0"
    )

    risks = must_dict_list(content["risks_mitigations"])
    assert risks[0]["severity"] == "medium"
    assert risks[0]["mitigation"] == "Reuse the canonical plan normalizer on the mapped content."

    verification = must_dict_list(content["verification_strategy"])
    assert verification[0] == {
        "method": "pytest tests/mcp/test_md_plan_spec.py -q",
        "expected_outcome": "focused tests pass",
        "timeout_seconds": 120,
    }


def test_plan_spec_preserves_descriptive_execution_vocabularies() -> None:
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
    assert {diagnostic.rule_id for diagnostic in diagnostics} == {"PLAN006"}
    summary = must_mapping(content["summary"])
    assert summary["intent_verb"] == "invented"
    assert summary["coverage_areas"] == ["feature", "invented"]
    scope_items = must_dict_list(summary["scope_items"])
    assert scope_items[0]["category"] == "invented"
    steps = _steps(content)
    assert steps[0]["step_type"] == "invented"
    first_target = must_dict_list(steps[0]["targets"])[0]
    assert first_target["action"] == "invented"
    assert first_target["path"] == "ralph/mcp/artifacts/markdown/specs/plan.py"
    risks = must_dict_list(content["risks_mitigations"])
    assert risks[0]["severity"] == "invented"


def test_canonical_evidence_kind_prefix_is_preserved_without_diagnostics() -> None:
    document = _plan_document().replace("- file: ralph", "- command_output: ralph")

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    steps = _steps(content)
    evidence = must_dict_list(steps[0]["expected_evidence"])
    assert evidence[0]["kind"] == "command_output"


def test_critical_file_action_is_free_form_descriptive_content() -> None:
    document = _plan_document().replace("Action: modify", "Action: inspect-only")

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    critical = must_mapping(content["critical_files"])
    primary = must_dict_list(critical["primary_files"])
    assert primary[0]["action"] == "inspect-only"


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


def test_dangling_step_and_criterion_references_are_line_anchored_warnings() -> None:
    """Dangling references surface as line-anchored warnings, not blocking errors.

    Under the plan-scoped severity policy, PLAN021 (dangling step /
    criterion reference) is content-shape and demoted to warning. The
    plan still maps to canonical content so downstream consumers see
    what the agent authored; the warning is line-anchored so the agent
    can see which step / criterion broke the reference.
    """
    document = (
        _plan_document()
        .replace("Depends on: S-1", "Depends on: S-9")
        .replace("Satisfied by: S-1", "Satisfied by: S-8")
    )

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert content != {}
    plan021 = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.rule_id == "PLAN021" and diagnostic.severity == "warning"
    ]
    assert {diagnostic.section for diagnostic in plan021} == {
        "Steps",
        "Acceptance Criteria",
    }
    assert all(diagnostic.line > 1 for diagnostic in plan021)


def test_dependency_cycles_advisories_with_cost_named() -> None:
    """A step-dependency cycle is a warning under the plan-scoped policy.

    REF004 (dependency cycle) is content-shape and demoted to warning.
    The plan still maps so the agent can see the cycle and decide
    whether to repair it or override the warning with a recorded reason.
    """
    document = _plan_document().replace(
        "Type: file_change",
        "Type: file_change\nDepends on: S-2",
    )

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert content != {}
    assert any(
        "cycle" in diagnostic.message and diagnostic.severity == "warning"
        for diagnostic in diagnostics
    )


def test_step_type_contracts_advisely_emit_step_anchored_diagnostics() -> None:
    no_files = _plan_document().replace(
        "Files:\n- modify ralph/mcp/artifacts/markdown/specs/plan.py\n"
        "- create tests/mcp/test_md_plan_spec.py\n",
        "",
    )
    no_verify = _plan_document().replace(
        "Verify: pytest tests/mcp/test_md_plan_spec.py -q\n"
        "Expect: the focused markdown-plan tests pass with exit code 0\n\n"
        "## Critical Files",
        "\n## Critical Files",
    )

    _content_no_files, files_diagnostics = parse_and_validate(no_files, PLAN_SPEC)
    _content_no_verify, verify_diagnostics = parse_and_validate(no_verify, PLAN_SPEC)

    # PLAN010 / PLAN011 are advisory: warnings, not errors. The pydantic
    # canonical model still rejects the missing target/command (SPEC010),
    # but the markdown-side finding is no longer an error.
    assert any(
        diagnostic.rule_id == "PLAN010"
        and diagnostic.severity == "warning"
        and diagnostic.section == "Steps"
        and "S-1" in diagnostic.message
        for diagnostic in files_diagnostics
    )
    assert any(
        diagnostic.rule_id == "PLAN011"
        and diagnostic.severity == "warning"
        and diagnostic.section == "Steps"
        and "S-2" in diagnostic.message
        for diagnostic in verify_diagnostics
    )


def test_only_consumed_verification_expectation_is_advisory_at_the_item_line() -> None:
    document = (
        _plan_document()
        .replace("  Mitigation: Reuse the canonical plan normalizer on the mapped content.\n", "")
        .replace("  Expect: focused tests pass\n", "")
    )

    _content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    # PLAN020 on the verification expectation gap is advisory; the canonical
    # schema still raises SPEC010 because expected_outcome is required by
    # VerificationStep, but the PLAN020 warning fires alongside it. The
    # pydantic branch of SPEC010 is itself demoted to warning under the
    # plan-scoped severity policy, so both findings are advisory.
    warnings = [diagnostic for diagnostic in diagnostics if diagnostic.severity == "warning"]
    assert {"PLAN020", "SPEC010"} <= {diagnostic.rule_id for diagnostic in warnings}
    assert {diagnostic.section for diagnostic in warnings if diagnostic.rule_id == "PLAN020"} == {
        "Verification"
    }
    assert any("Expect" in diagnostic.message for diagnostic in warnings)


def test_malformed_and_duplicate_step_ids_are_rejected() -> None:
    duplicate = _plan_document().replace(
        "### [S-2] Verify the focused suites", "### [S-1] Verify the focused suites"
    )
    malformed = _plan_document().replace("### [S-2]", "### [STEP-2]")

    _, duplicate_diagnostics = parse_and_validate(duplicate, PLAN_SPEC)
    _, malformed_diagnostics = parse_and_validate(malformed, PLAN_SPEC)

    assert any(
        diagnostic.rule_id == "PLAN022" and "duplicate step ID 'S-1'" in diagnostic.message
        for diagnostic in duplicate_diagnostics
    )
    assert any(
        diagnostic.rule_id == "PLAN022" and "STEP-2" in diagnostic.message
        for diagnostic in malformed_diagnostics
    )


def test_shell_invocation_guard_advisories_with_cost_named() -> None:
    """A shell-prefixed verification advisories a bounded-exec safety warning.

    Under the plan-scoped severity policy, the shell-prefixed PLAN020
    finding is content-shape and demoted to warning. The plan still
    maps to canonical content; the warning names the run cost (the
    bounded-exec safety policy forbids shell interpreter invocations
    and a shell-prefixed command bypasses the policy at every
    subprocess call site) and the fix.
    """
    document = _plan_document().replace(
        "- [V-1] pytest tests/mcp/test_md_plan_spec.py -q",
        "- [V-1] bash -c 'pytest tests'",
    )

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert content != {}
    assert any(
        "shell interpreter" in diagnostic.message and diagnostic.severity == "warning"
        for diagnostic in diagnostics
    )


def test_partial_free_shape_document_remains_parseable() -> None:
    truncated = _plan_document().split("Satisfies: AC-01")[0]

    content, diagnostics = parse_and_validate(truncated, PLAN_SPEC)

    assert content["steps"]
    assert not [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]


def test_two_independent_subplans_with_repeated_sections_validate() -> None:
    document = """---
type: plan
---
## Subplan Alpha
### [S-1] Implement alpha
Change the alpha component.

Type: file_change
Files:
- modify src/alpha.py

## Acceptance Criteria
- [AC-01] Alpha behavior is observable
  Satisfied by: S-1
  Evidence: src/alpha.py

## Subplan Beta
### [S-2] Implement beta
Change the beta component.

Type: file_change
Files:
- modify src/beta.py

## Acceptance Criteria
- [AC-02] Beta behavior is observable
  Satisfied by: S-2
  Verify: pytest tests/test_beta.py -q
  Expect: the beta tests pass with exit code 0
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    assert [step["number"] for step in _steps(content)] == [1, 2]


def test_work_units_can_each_contain_nested_mini_plan_steps() -> None:
    document = """---
type: plan
---
## Work Units
- [alpha] Implement alpha independently
  Directories: src/alpha
- [beta] Implement beta after alpha
  Directories: src/beta

## Alpha Mini Plan
### [S-1] Implement alpha
Change the alpha component.

Type: file_change
Files:
- modify src/alpha/main.py

## Beta Mini Plan
### [S-2] Implement beta
Change the beta component.

Type: file_change
Files:
- modify src/beta/main.py
Depends on: S-1
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    assert [step["number"] for step in _steps(content)] == [1, 2]
    assert content["work_units"] == [
        {
            "unit_id": "alpha",
            "description": "Implement alpha independently",
            "allowed_directories": ["src/alpha"],
            "step_ids": ["S-1"],
        },
        {
            "unit_id": "beta",
            "description": "Implement beta after alpha",
            "allowed_directories": ["src/beta"],
            "dependencies": ["alpha"],
            "step_ids": ["S-2"],
        },
    ]


@pytest.mark.parametrize("section", ["Work Units", "Parallel Plan"])
def test_parallel_unit_dependencies_advisory_with_cost_named(section: str) -> None:
    """An unknown unit dependency is a content-shape warning.

    REF003 (unknown reference) is demoted to warning under the
    plan-scoped severity policy. The plan still maps so the agent can
    see the unknown reference and decide whether to repair it or
    override the warning with a recorded reason.
    """
    document = f"""---
type: plan
---
## Steps
### [S-1] Implement the change
Apply the bounded change across the validation entry points so the
existing tests in the regression suite continue to pass after the change.

## {section}
- [alpha] Implement alpha across the existing endpoints and tests
  Directories: src/alpha
  Depends on: missing
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert content != {}
    assert any(
        diagnostic.section == section
        and "unknown" in diagnostic.message.casefold()
        and "missing" in diagnostic.message
        and diagnostic.severity == "warning"
        for diagnostic in diagnostics
    )


@pytest.mark.parametrize("section", ["Work Units", "Parallel Plan"])
def test_consumed_parallel_fields_advisory_with_cost_named(section: str) -> None:
    """A missing Directories value is a content-shape warning.

    PLAN020 (fan-out field strictness) is demoted to warning under the
    plan-scoped severity policy. The plan still maps; the warning names
    the run cost (the worker fan-out silently drops the unit's
    directory set) and the fix (rewrite the field shape).
    """
    document = f"""---
type: plan
---
## Steps
### [S-1] Implement the change
Apply the bounded change across the validation entry points so the
existing tests in the regression suite continue to pass after the change.

## {section}
- [alpha] Implement alpha across the existing endpoints and tests
  Directories:

## Notes
The plan continues after the incomplete field so the advisory parser can report it.
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert content != {}
    assert any(
        diagnostic.section == section
        and diagnostic.rule_id == "PLAN020"
        and "requires a value" in diagnostic.message
        and diagnostic.severity == "warning"
        for diagnostic in diagnostics
    )


@pytest.mark.parametrize("section", ["Work Units", "Parallel Plan"])
def test_parallel_unit_dependencies_advisory_cycle(section: str) -> None:
    """A work-unit dependency cycle is a content-shape warning.

    REF004 (dependency cycle) is demoted to warning under the
    plan-scoped severity policy. The plan still maps; the warning names
    the run cost (the fan-out dispatch cannot form a DAG) and the fix.
    """
    document = f"""---
type: plan
---
## Steps
### [S-1] Implement the change
Apply the bounded change.

## {section}
- [alpha] Implement alpha
  Directories: src/alpha
  Depends on: beta
- [beta] Implement beta
  Directories: src/beta
  Depends on: alpha
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert content != {}
    assert any(
        diagnostic.section == section
        and "cycle" in diagnostic.message.casefold()
        and diagnostic.severity == "warning"
        for diagnostic in diagnostics
    )


def test_step_ids_are_unique_across_nested_mini_plans() -> None:
    """A duplicate step ID is a PLAN022 warning under the new contract.

    Under the plan-scoped severity policy, PLAN022 (malformed /
    duplicate step ID) is content-shape and demoted to warning. The
    plan still maps; the warning names the run cost (development_result
    proof cross-references collide) and the fix.
    """
    document = """---
type: plan
---
## Alpha Mini Plan
### [S-1] Implement alpha
Alpha work across the validation entry points so the existing tests in
the regression suite continue to pass after the change.

## Beta Mini Plan
### [S-1] Implement beta
Beta work across the validation entry points so the existing tests in
the regression suite continue to pass after the change.
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert content != {}
    assert any(
        diagnostic.rule_id == "PLAN022"
        and "S-1" in diagnostic.message
        and diagnostic.severity == "warning"
        for diagnostic in diagnostics
    )


def test_unfamiliar_plan_shape_validates_when_consumed_anchors_are_parseable() -> None:
    document = """---
type: plan
---
## Expedition Ledger
This intentionally resembles none of the recommended plan outlines.

### [S-7] Cross the first ridge
Inspect the existing route and record the result.

Location: docs/route.md

## A Completely Different Chapter
### [S-42] Cross the second ridge
Update the route after the inspection.

Type: file_change
Depends on: S-7
Files:
- modify docs/route.md
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    assert [step["number"] for step in _steps(content)] == [7, 42]


def test_acceptance_criterion_must_advisely_name_evaluatable_evidence_or_command() -> None:
    document = """---
type: plan
---
## Any Shape
### [S-1] Implement the change
Make the behavior observable across the validation entry points so the
existing tests in the regression suite continue to pass after the change.

## Acceptance Criteria
- [AC-01] The code is clean across the validation entry points
  Satisfied by: S-1
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    # Missing Verify/Evidence is advisory (warning), not blocking: the plan
    # still maps to canonical content and the analysis phase owns the
    # substance check.
    assert content != {}
    assert any(
        diagnostic.rule_id == "PLAN020"
        and diagnostic.severity == "warning"
        and diagnostic.section == "Acceptance Criteria"
        and "Verify" in diagnostic.message
        and "Evidence" in diagnostic.message
        for diagnostic in diagnostics
    )


def test_inserting_and_moving_steps_keeps_ids_and_references_stable() -> None:
    document = _plan_document()
    new_step = (
        "### [S-3] Document the grammar\n"
        "Summarize the grammar for reconciliation.\n\n"
        "Type: action\n"
        "Depends on: S-2\n"
    )
    inserted = document.rstrip("\n") + "\n\n" + new_step

    content, diagnostics = parse_and_validate(inserted, PLAN_SPEC)

    assert diagnostics == []
    steps = _steps(content)
    assert [step["number"] for step in steps] == [1, 2, 3]
    assert steps[2]["depends_on"] == [2]


def test_removing_a_referenced_step_is_rejected_and_leaves_input_valid() -> None:
    document = _plan_document()
    broken = document.replace("### [S-1]", "### [S-99]")
    _, diagnostics = parse_and_validate(broken, PLAN_SPEC)
    assert any(diagnostic.rule_id == "PLAN021" for diagnostic in diagnostics)
