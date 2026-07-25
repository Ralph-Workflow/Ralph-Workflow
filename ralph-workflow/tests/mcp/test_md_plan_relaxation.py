"""Regression coverage for relaxed, free-shape Markdown plans."""

from __future__ import annotations

import pytest

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs import PLAN_SPEC
from tests._support.typed_accessors import (
    must_dict_list,
    must_mapping,
)


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
@pytest.mark.parametrize(
    "bad_id",
    ["STEP-2", "STEP-X", "S-0", "S-01", "S-X", "S-1a", "s-2"],
)
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
    steps = must_dict_list(content["steps"])
    assert steps[0]["title"] == "Implement the change"


def test_plan_grammar_regression_ac_items_are_discovered_outside_named_section() -> None:
    """Regression for plan blocker 3: unambiguous AC items are document-wide."""
    document = _single_step_document(extra="Satisfies: AC-01\n") + """

## Product Outcomes
- [AC-01] The focused suite proves the behavior
  Verify: pytest tests/mcp/test_md_plan_relaxation.py -q
  Expect: the focused plan-relaxation tests pass with exit code 0
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    design = must_mapping(content["design"])
    acceptance = must_mapping(design["acceptance_criteria"])
    criteria = must_dict_list(acceptance["criteria"])
    assert criteria == [
        {
            "id": "AC-01",
            "description": "The focused suite proves the behavior",
            "verification_step": "pytest tests/mcp/test_md_plan_relaxation.py -q",
            "expected_outcome": "the focused plan-relaxation tests pass with exit code 0",
        }
    ]


def test_plan_grammar_discovers_criterion_after_nested_step() -> None:
    """A criterion after a step remains a section item, not step prose."""
    document = """---
type: plan
---
## API Subplan

### [S-1] Implement the API
Add the endpoint behavior.

Type: action

- [AC-01] The API contract is proven
  Verify: pytest tests/api/test_contract.py -q
  Expect: the focused API contract tests pass with exit code 0
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    steps = must_dict_list(content["steps"])
    assert steps == [
        {
            "number": 1,
            "title": "Implement the API",
            "content": "Add the endpoint behavior.",
        }
    ]
    design = must_mapping(content["design"])
    acceptance = must_mapping(design["acceptance_criteria"])
    criteria = must_dict_list(acceptance["criteria"])
    assert criteria == [
        {
            "id": "AC-01",
            "description": "The API contract is proven",
            "verification_step": "pytest tests/api/test_contract.py -q",
            "expected_outcome": "the focused API contract tests pass with exit code 0",
        }
    ]


@pytest.mark.parametrize("section", ["Work Units", "Parallel Plan"])
def test_exact_fan_out_section_rejects_malformed_unit_marker(section: str) -> None:
    """An exact consumed heading must not silently degrade to descriptive prose."""
    document = _single_step_document() + f"""

## {section}
- api: Implement the API
  Directories: src/api
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert content == {}
    assert any(
        diagnostic.rule_id == "PLAN024"
        and diagnostic.section == section
        and "stable-ID item" in diagnostic.message
        for diagnostic in diagnostics
    )


def test_custom_fan_out_lookalike_remains_descriptive() -> None:
    """Only exact consumed section names opt into fail-closed fan-out grammar."""
    document = _single_step_document() + """

## work units
- api: This lowercase heading is ordinary descriptive prose.
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    assert "work_units" not in content


def test_acceptance_criterion_inside_work_units_is_not_a_phantom_unit() -> None:
    """Criterion IDs retain criterion semantics inside a nested mini-plan."""
    document = """---
type: plan
---
## Work Units
- [api] Implement the API
  Directories: src/api
- [AC-01] The API report proves completion
  Evidence: reports/api-proof.json

### [S-1] Implement the API
Add the endpoint behavior.

Type: action
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    work_units = must_dict_list(content["work_units"])
    assert [unit["unit_id"] for unit in work_units] == ["api"]
    assert work_units[0]["step_ids"] == ["S-1"]
    design = must_mapping(content["design"])
    acceptance = must_mapping(design["acceptance_criteria"])
    criteria = must_dict_list(acceptance["criteria"])
    assert criteria[0]["id"] == "AC-01"


@pytest.mark.parametrize(
    ("criterion_field", "verification_expect"),
    [
        ("Verify: code is clean\n  Expect: exit code 0", "exit code 0"),
        ("Verify: no problems\n  Expect: exit code 0", "exit code 0"),
        ("Evidence: clean", "exit code 0"),
        (
            "Verify: pytest tests/mcp/test_md_plan_relaxation.py -q\n"
            "  Expect: the focused tests pass with exit code 0",
            "looks good",
        ),
    ],
    ids=["subjective-command", "outcome-as-command", "vague-evidence", "vague-outcome"],
)
def test_plan_grammar_regression_evaluatable_claims_advisory_must_be_concrete(
    criterion_field: str, verification_expect: str
) -> None:
    """PLAN020 is advisory: subjective proof text is a warning, not a blocker.

    The analysis phase owns the substance check; the validator advises.
    """
    document = _single_step_document() + f"""

## Acceptance Criteria
- [AC-01] The behavior is proven
  {criterion_field}

## Verification
- [V-1] pytest tests/mcp/test_md_plan_relaxation.py -q
  Expect: {verification_expect}
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    # Content still maps; the vague-proof findings are warnings, not errors.
    assert content != {}
    warnings = [diagnostic for diagnostic in diagnostics if diagnostic.severity == "warning"]
    assert any(
        diagnostic.rule_id == "PLAN020"
        and "concrete" in diagnostic.message
        for diagnostic in warnings
    )


def test_plan_grammar_regression_concrete_commands_and_artifacts_remain_valid() -> None:
    """Positive coverage for plan blocker 4's command and artifact proof paths."""
    document = _single_step_document() + """

## Product Outcomes
- [AC-01] The command proves the behavior
  Verify: pytest tests/mcp/test_md_plan_relaxation.py -q
  Expect: the focused plan-relaxation tests pass with exit code 0
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
    assert len(content["verification_strategy"]) == 2


def test_semantic_verification_item_under_arbitrary_heading_advisories_vague_proof() -> None:
    """A V-n proof item stays evaluatable without a conventional heading.

    PLAN020 on the vague verification method is advisory: it warns, it does
    not block. The analysis phase owns the substance check.
    """
    document = """---
type: plan
---
## Expedition Ledger

### [S-1] Change it
Implement bounded behavior.

## Proof Matrix
- [V-1] check it manually
  Expect: everything works
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert content != {}
    warnings = [diagnostic for diagnostic in diagnostics if diagnostic.severity == "warning"]
    assert any(
        diagnostic.rule_id == "PLAN020"
        and diagnostic.severity == "warning"
        and diagnostic.section == "Verification"
        and "concrete command or file/artifact inspection" in diagnostic.message
        for diagnostic in warnings
    )


def test_semantic_verification_items_preserve_radically_different_valid_shape() -> None:
    """Concrete V-n proof items map under arbitrary Unicode headings."""
    document = """---
type: plan
---
## Équipe — changement

### [S-1] Bound the behavior
Implement bounded behavior.

## 証拠・Proof Matrix
- [V-7] pytest tests/mcp/test_md_plan_relaxation.py -q
  Expect: the focused plan-relaxation tests pass with exit code 0
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    assert content["verification_strategy"] == [
        {
            "method": "pytest tests/mcp/test_md_plan_relaxation.py -q",
            "expected_outcome": (
                "the focused plan-relaxation tests pass with exit code 0"
            ),
        }
    ]


def test_unrelated_v_prefixed_item_remains_descriptive() -> None:
    """A non-semantic V-prefixed ID must not become a verification contract."""
    document = _single_step_document() + """

## Release Notes
- [VERSION-1] Verification terminology changed in this release.
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    assert "verification_strategy" not in content


def test_plan_grammar_regression_command_proof_requires_specific_expected_output() -> None:
    """A runnable command alone does not say what observable result proves success."""
    document = _single_step_document(
        extra=(
            "Verify: git diff --check\n"
            "Expect: no problems\n"
        )
    ) + """

## Acceptance Criteria
- [AC-01] The diff check proves the patch is well formed
  Verify: git diff --check
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert content == {}
    assert sum(
        diagnostic.rule_id == "PLAN020"
        and "Expect:" in diagnostic.message
        for diagnostic in diagnostics
    ) >= 2


def test_plan_grammar_regression_arbitrary_executable_with_specific_output_is_valid() -> None:
    """Evaluatability must not depend on a short hard-coded executable allowlist."""
    document = _single_step_document(
        extra=(
            "Verify: dotnet test tests/Plan.Tests/Plan.Tests.csproj\n"
            "Expect: Plan.Tests reports 12 passed tests and exit code 0\n"
        )
    ) + """

## Acceptance Criteria
- [AC-01] The .NET regression suite proves the behavior
  Verify: dotnet test tests/Plan.Tests/Plan.Tests.csproj
  Expect: Plan.Tests reports 12 passed tests and exit code 0
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    steps = must_dict_list(content["steps"])
    assert steps[0]["expected_outcome"] == (
        "Plan.Tests reports 12 passed tests and exit code 0"
    )


def test_legacy_exact_command_reuses_global_verification_expectation() -> None:
    """Preserve old plans that centralize one exact command/outcome pair."""
    document = _single_step_document(
        extra="Verify: pytest tests/mcp/test_md_plan_relaxation.py -q\n"
    ) + """

## Acceptance Criteria
- [AC-01] The focused suite proves the behavior
  Verify: pytest tests/mcp/test_md_plan_relaxation.py -q

## Verification
- [V-1] pytest tests/mcp/test_md_plan_relaxation.py -q
  Expect: the focused plan-relaxation tests pass with exit code 0
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    steps = must_dict_list(content["steps"])
    assert steps[0]["expected_outcome"] == (
        "the focused plan-relaxation tests pass with exit code 0"
    )


def test_kimi_regression_multiline_scope_and_descriptive_design_values_validate() -> None:
    """Pin the Kimi plan shape that motivated full descriptive-field relaxation."""
    document = """---
type: plan
---
## Scope
The change spans the parser and the generated reference material.
The executor may choose the smallest safe internal decomposition.

## Design
Profile: thorough
Architecture: event-driven modular monolith
Black box: when practical
Clock injection: only where timing is observable

## Delivery
### [S-1] Implement the requested behavior
Apply the scoped change and retain compatibility.

Type: action
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert not [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    assert must_dict_list(content["steps"])[0]["number"] == 1


def test_strict_profile_remains_descriptive_and_does_not_inject_contracts() -> None:
    """A descriptive profile cannot fabricate project-specific execution requirements."""
    document = _single_step_document() + """

## Design
Profile: strict
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    assert content["design"] == {"planning_profile": "strict"}


def test_minimax_regression_preserves_nested_descriptive_plan_vocabulary() -> None:
    """Pin the MiniMax shape: nested proof anchors plus project vocabulary."""
    document = """---
type: plan
---
## Work Units
- [api-gate] Implement and prove the API slice
  Directories: src/api, tests/api

### [S-10] Implement the API slice
Apply the repository-specific integration behavior.

Type: integration_gate
Files:
- inspect src/api/routes.py
- coordinate tests/api/test_routes.py

- [AC-10] The generated API report proves the contract
  Satisfied by: S-10
  Evidence: reports/minimax-api-proof.json

## Critical Files
- [CF-10] src/api/routes.py
  Action: inspect-only

## Design
Profile: strict
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    steps = must_dict_list(content["steps"])
    assert steps[0]["step_type"] == "integration_gate"
    assert steps[0]["targets"] == [
        {"path": "src/api/routes.py", "action": "inspect"},
        {"path": "tests/api/test_routes.py", "action": "coordinate"},
    ]
    work_units = must_dict_list(content["work_units"])
    assert [unit["unit_id"] for unit in work_units] == ["api-gate"]
    assert work_units[0]["step_ids"] == ["S-10"]
    critical_files = must_mapping(content["critical_files"])
    primary_files = must_dict_list(critical_files["primary_files"])
    assert primary_files[0]["action"] == "inspect-only"
    assert content["design"] == {
        "planning_profile": "strict",
        "acceptance_criteria": {
            "criteria": [
                    {
                        "id": "AC-10",
                        "description": "The generated API report proves the contract",
                        "evidence_path": "reports/minimax-api-proof.json",
                        "satisfied_by_steps": [10],
                    }
            ]
        },
    }


def test_unconsumed_design_vocabularies_are_advisory() -> None:
    """Recognized descriptive labels accept project-specific vocabulary."""
    document = """---
type: plan
---
## Design
Constraints: Preserve the product contract.
Architecture: event-streamed plugin host
Black box: yes
Forbidden in tests: project-harness
Test layers: executable-spec
DI required: yes
DI preferred: capability-object
DI forbidden: ambient-registry
Guard commands:
- custom-lint --strict
Drift sources: custom-lint
On drift: reconcile-in-place
Refactor approach: surgical-replacement
Dead code: archive-after-release

## Execution
### [S-1] Apply the design
Implement the bounded plan.
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert not [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    assert must_dict_list(content["steps"])[0]["number"] == 1


def test_unconsumed_descriptive_sections_and_fields_never_become_requirements() -> None:
    """Optional descriptive structure may be incomplete without blocking execution."""
    document = """---
type: plan
intent_verb: fix
---
## Skills MCP
Use whichever repository skills are relevant after inspecting the task.

## Scope
- [SC-1] Refresh the user guide
  Category: docs

## Critical Files
- [CF-1] docs/reference.md
  Purpose: background material only

## Risks
- [R-1] The prose could become stale

## Execution
### [S-1] Apply the bounded change
Type: action

## Acceptance Criteria
- [AC-01] The referenced artifact remains inspectable
  Satisfied by: S-1
  Evidence: docs/reference.md
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert not [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    steps = must_dict_list(content["steps"])
    assert steps == [{"number": 1, "title": "Apply the bounded change"}]


def test_custom_descriptive_sections_do_not_create_unconsumed_id_rules() -> None:
    """Arbitrary narrative item IDs are not part of the execution namespace."""
    document = """---
type: plan
coordination_style: project-specific
---
## Decision Journal
- [NOTE-1] First perspective on the implementation.
- [NOTE-1] A second perspective may reuse a purely descriptive marker.

Local vocabulary: project-specific and intentionally unstructured

### [S-1] Apply the resolved decision
Implement the bounded result recorded by the plan.
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    assert must_dict_list(content["steps"])[0]["number"] == 1


def test_work_unit_descriptive_labels_do_not_block_consumed_fields() -> None:
    """Extra unit metadata is prose; declared directories remain machine parsed."""
    document = """---
type: plan
---
## Work Units
- [api] Implement the API slice
  Owner: API execution subagent
  Directories: src/api

### [S-1] Implement the API slice
Type: file_change
Files:
- modify src/api/routes.py
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert not [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    assert [diagnostic.rule_id for diagnostic in diagnostics] == ["PLAN009"]
    units = must_dict_list(content["work_units"])
    assert units[0]["allowed_directories"] == ["src/api"]
    assert units[0]["step_ids"] == ["S-1"]


def test_multiple_work_units_in_one_section_own_following_nested_steps() -> None:
    """Each WU item owns the nested steps that follow it until the next WU item."""
    document = """---
type: plan
---
## Work Units
- [api] Implement the API slice
  Directories: src/api

### [S-1] Implement the API
Type: action

- [web] Implement the web slice
  Directories: src/web

### [S-2] Implement the web client
Type: action
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    units = must_dict_list(content["work_units"])
    assert units == [
        {
            "unit_id": "api",
            "description": "Implement the API slice",
            "allowed_directories": ["src/api"],
            "step_ids": ["S-1"],
        },
        {
            "unit_id": "web",
            "description": "Implement the web slice",
            "allowed_directories": ["src/web"],
            "step_ids": ["S-2"],
        },
    ]


def test_explicit_work_unit_owns_steps_under_nested_descriptive_headings() -> None:
    """A work-unit item owns descendant steps across heading-depth changes."""
    document = """---
type: plan
---
## Work Units
- [api] Implement the API slice
  Directories: src/api

### Authentication
#### [S-1] Implement authentication
Type: file_change
Files:
- modify src/api/auth.py
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    units = must_dict_list(content["work_units"])
    assert units[0]["step_ids"] == ["S-1"]


def test_subplan_owns_steps_under_nested_descriptive_headings() -> None:
    """A named execution subplan includes steps in all descendant headings."""
    document = """---
type: plan
---
## API Subplan
### Authentication
#### [S-1] Implement authentication
Type: file_change
Files:
- modify src/api/auth.py
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    units = must_dict_list(content["work_units"])
    assert units == [
        {
            "unit_id": "subplan-s-1",
            "description": "API",
            "allowed_directories": ["src/api"],
            "step_ids": ["S-1"],
        }
    ]


def test_subplan_prefix_suffix_mapping_remaps_cross_subplan_dependencies() -> None:
    """Prefix/suffix headings normalize alike and later-step edges map to unit IDs."""
    document = """---
type: plan
---
## Subplan: API / Core
### [S-10] Implement the API
Add the API behavior.

Type: file_change
Files:
- modify src/api/routes.py

### [S-11] Prove the API
Run the API contract test.

Type: verify
Depends on: S-10
Verify: pytest tests/api/test_routes.py -q
Expect: the API route tests pass with exit code 0

## WEB SUB-PLAN
### [S-20] Implement the web client
Add the web behavior.

Type: file_change
Files:
- modify src/web/client.ts

### [S-21] Reconcile the API contract
Use the proven API behavior from the other execution subplan.

Depends on: S-11

## Integration and Verification Subplan
### [S-30] Fan in the execution subplans
Integrate both outputs in the main session.

Depends on: S-11, S-21

### [S-31] Prove the integrated result
Run the integrated check.

Type: verify
Depends on: S-30
Verify: pytest tests/integration/test_web_api.py -q
Expect: the integrated web API test passes with exit code 0
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert diagnostics == []
    units = must_dict_list(content["work_units"])
    assert units == [
        {
            "unit_id": "subplan-s-10",
            "description": "API / Core",
            "allowed_directories": ["src/api"],
            "step_ids": ["S-10", "S-11"],
        },
        {
            "unit_id": "subplan-s-20",
            "description": "WEB",
            "allowed_directories": ["src/web"],
            "dependencies": ["subplan-s-10"],
            "step_ids": ["S-20", "S-21"],
        },
    ]


def test_plan_grammar_regression_repeated_work_units_retain_nested_step_ownership() -> None:
    """Regression for plan blocker 6: normalized units retain nested mini-plan IDs."""
    content, diagnostics = parse_and_validate(_five_nested_work_units_document(), PLAN_SPEC)

    assert diagnostics == []
    work_units = must_dict_list(content["work_units"])
    assert [unit["step_ids"] for unit in work_units] == [
        ["S-1"],
        ["S-2"],
        ["S-3"],
        ["S-4"],
        ["S-5"],
    ]
