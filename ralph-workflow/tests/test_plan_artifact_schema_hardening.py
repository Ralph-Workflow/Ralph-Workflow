"""Schema-hardening tests for the plan-artifact package.

The tests in this module cover the four cross-section
validators added to ``PlanArtifact``, the typed ``EvidenceRef`` /
``PlanConstraints`` sub-models, the new ``timeout_seconds`` / ``cwd``
fields on ``VerificationStep``, and format-doc synchronization.
The retired dict-to-markdown renderer is intentionally not covered:
native ``plan.md`` is now the source of truth. All tests are pure
Pydantic round-trips with no
``time.sleep``, no real subprocess, and no real file I/O so the
``audit_test_policy`` guard accepts them and the 60-second combined
test budget stays well within budget.
"""

from __future__ import annotations

import pytest

from ralph.mcp.artifacts.format_docs import load_bundled_format_doc
from ralph.mcp.artifacts.plan import (
    PLAN_SECTION_OBJECT_MODELS,
    EvidenceRef,
    PlanArtifact,
    PlanArtifactValidationError,
    PlanConstraints,
    VerificationStep,
    normalize_plan_artifact_content,
    validate_plan_section,
)
from tests._support.typed_accessors import (
    must_dict_list,
    must_mapping,
)


def _base_plan_dict() -> dict[str, object]:
    """Minimal valid plan dict used by the cross-section validator tests."""
    return {
        "summary": {
            "context": "c",
            "scope_items": [
                {"text": "alpha", "category": "feature"},
                {"text": "bravo", "category": "file_change"},
                {"text": "charlie", "category": "docs"},
            ],
        },
        "skills_mcp": {"skills": ["x"], "mcps": []},
        "steps": [
            {
                "number": 1,
                "title": "Implement",
                "content": "Implement the feature.",
                "step_type": "file_change",
                "targets": [{"path": "src/a.py", "action": "modify"}],
            }
        ],
        "critical_files": {
            "primary_files": [{"path": "src/a.py", "action": "modify"}],
        },
        "risks_mitigations": [{"risk": "r", "mitigation": "m"}],
        "verification_strategy": [
            {"method": "pytest tests/test_x.py -q", "expected_outcome": "pass"}
        ],
    }


def _cast_summary(
    plan: dict[str, object],
    *,
    intent_verb: str,
    scope_categories: list[str],
) -> dict[str, object]:
    """Build a Summary dict with the given intent_verb and scope_items categories."""
    return {
        "intent": "",
        "intent_verb": intent_verb,
        "context": "c",
        "scope_items": [
            {"text": f"item-{idx}", "category": category}
            for idx, category in enumerate(scope_categories)
        ],
    }


# ---------------------------------------------------------------------------
# 1. test_evidence_ref_round_trip
# ---------------------------------------------------------------------------


def test_evidence_ref_round_trip() -> None:
    """EvidenceRef round-trips through model_validate + model_dump(exclude_defaults=True)."""
    ref = EvidenceRef(kind="file", ref="x.py")
    dumped = ref.model_dump(exclude_defaults=True)
    assert dumped == {"kind": "file", "ref": "x.py"}
    reloaded = EvidenceRef.model_validate(dumped)
    assert reloaded == ref


# ---------------------------------------------------------------------------
# 2. test_evidence_ref_rejects_unknown_kind
# ---------------------------------------------------------------------------


def test_evidence_ref_rejects_unknown_kind() -> None:
    """EvidenceRef rejects a kind that is not in {file, command_output, test_name}."""
    with pytest.raises(ValueError):
        EvidenceRef.model_validate({"kind": "weird", "ref": "x"})


# ---------------------------------------------------------------------------
# 3. test_evidence_ref_max_length
# ---------------------------------------------------------------------------


def test_evidence_ref_max_length() -> None:
    """EvidenceRef.ref max_length is 1000, NOT 200."""
    with pytest.raises(ValueError):
        EvidenceRef.model_validate({"kind": "file", "ref": "x" * 1001})


# ---------------------------------------------------------------------------
# 4. test_evidence_ref_rejects_bare_string
# ---------------------------------------------------------------------------


def test_evidence_ref_rejects_bare_string() -> None:
    """Evidence entries require the structured shape emitted by the Markdown parser."""
    with pytest.raises(ValueError):
        EvidenceRef.model_validate("foo.py")


@pytest.mark.parametrize("alias", ["test", "tests", "check", "run"])
def test_plan_step_preserves_project_specific_step_types(alias: str) -> None:
    """Step types are descriptive unless they name a built-in contract."""
    plan = _base_plan_dict()
    steps = plan["steps"]
    assert isinstance(steps, list)
    step = steps[0]
    assert isinstance(step, dict)
    step["step_type"] = alias

    normalized = normalize_plan_artifact_content(plan)

    normalized_steps = must_dict_list(normalized["steps"])
    assert normalized_steps[0]["step_type"] == alias


def test_canonical_plan_rejects_duplicate_consumed_step_numbers() -> None:
    """Direct canonical payloads share Markdown's document-wide step namespace."""
    plan = _base_plan_dict()
    steps = must_dict_list(plan["steps"])
    steps.append(
        {
            "number": 1,
            "title": "Duplicate",
            "content": "This must not shadow the first consumed step.",
        }
    )

    with pytest.raises(
        PlanArtifactValidationError,
        match=r"duplicate plan step number 1",
    ):
        normalize_plan_artifact_content(plan)


def test_canonical_plan_rejects_dangling_step_dependencies() -> None:
    """A canonical dependency must resolve just like a Markdown ``S-n`` edge."""
    plan = _base_plan_dict()
    steps = must_dict_list(plan["steps"])
    steps[0]["depends_on"] = [99]

    with pytest.raises(
        PlanArtifactValidationError,
        match=r"plan step 1 depends on unknown step 99",
    ):
        normalize_plan_artifact_content(plan)


def test_canonical_step_command_requires_an_expected_outcome() -> None:
    """The runtime model cannot bypass Markdown's command-plus-outcome pair."""
    plan = _base_plan_dict()
    steps = must_dict_list(plan["steps"])
    steps[0]["verify_command"] = "pytest tests/test_x.py -q"

    with pytest.raises(
        PlanArtifactValidationError,
        match=r"verify_command must declare expected_outcome",
    ):
        normalize_plan_artifact_content(plan)


def test_canonical_acceptance_command_requires_an_expected_outcome() -> None:
    """Acceptance commands retain the same evaluator contract after mapping."""
    plan = _base_plan_dict()
    plan["design"] = {
        "acceptance_criteria": {
            "criteria": [
                {
                    "id": "AC-01",
                    "description": "The focused behavior is proven.",
                    "verification_step": "pytest tests/test_x.py -q",
                    "satisfied_by_steps": [1],
                }
            ]
        }
    }

    with pytest.raises(
        PlanArtifactValidationError,
        match=r"verification_step must declare expected_outcome",
    ):
        normalize_plan_artifact_content(plan)


# ---------------------------------------------------------------------------
# 5. test_plan_constraints_dedupes_case_insensitively
# ---------------------------------------------------------------------------


def test_plan_constraints_dedupes_case_insensitively() -> None:
    """PlanConstraints.must_not_break dedupes case-insensitively (last-wins)."""
    c = PlanConstraints(must_not_break=["API", "api", "API2"])
    assert c.must_not_break == ["API", "API2"]


# ---------------------------------------------------------------------------
# 6. test_plan_constraints_drops_empty_entries
# ---------------------------------------------------------------------------


def test_plan_constraints_drops_empty_entries() -> None:
    """PlanConstraints.must_not_break drops empty / whitespace-only entries."""
    c = PlanConstraints(must_not_break=["", "x", "  "])
    assert c.must_not_break == ["x"]


# ---------------------------------------------------------------------------
# 7. test_plan_constraints_section_registered
# ---------------------------------------------------------------------------


def test_plan_constraints_section_registered() -> None:
    """PLAN_SECTION_OBJECT_MODELS['constraints'] is PlanConstraints; section validates."""
    assert PLAN_SECTION_OBJECT_MODELS["constraints"] is PlanConstraints
    normalized = validate_plan_section(
        "constraints", {"must_not_break": ["public API"]}, mode="replace"
    )
    assert isinstance(normalized, dict)
    assert normalized["must_not_break"] == ["public API"]


# ---------------------------------------------------------------------------
# 8. test_noop_field_on_plan_artifact
# ---------------------------------------------------------------------------


def test_noop_field_on_plan_artifact() -> None:
    """noop field is a typed bool | None, default None, excluded from dumps."""
    field = PlanArtifact.model_fields["noop"]
    assert field.default is None
    normalized = normalize_plan_artifact_content(_base_plan_dict())
    assert "noop" not in normalized


# ---------------------------------------------------------------------------
# 9. test_intent_verb_scope_categories_are_descriptive
# ---------------------------------------------------------------------------


def test_intent_verb_scope_categories_are_descriptive() -> None:
    """Intent/category combinations do not control any runtime behavior."""
    plan = _base_plan_dict()
    summary = _cast_summary(
        plan, intent_verb="fix", scope_categories=["feature", "bugfix", "unknown"]
    )
    plan["summary"] = summary
    normalized = normalize_plan_artifact_content(plan)
    normalized_summary = must_mapping(normalized["summary"])
    assert normalized_summary["intent_verb"] == "fix"
    assert normalized_summary["scope_items"] == summary["scope_items"]

    summary = _cast_summary(
        plan, intent_verb="add", scope_categories=["bugfix", "feature", "infra"]
    )
    plan["summary"] = summary
    normalized = normalize_plan_artifact_content(plan)
    normalized_summary = must_mapping(normalized["summary"])
    assert normalized_summary["intent_verb"] == "add"
    assert normalized_summary["scope_items"] == summary["scope_items"]


def test_all_unconsumed_plan_vocabularies_accept_project_specific_values() -> None:
    """Descriptive metadata stays useful without becoming an execution gate."""
    plan = _base_plan_dict()
    plan["summary"] = {
        "intent_verb": "ship_it",
        "coverage_areas": ["operator-experience"],
        "scope_items": [
            {"text": "Refresh the operator flow", "category": "product-polish"}
        ],
    }
    steps = must_dict_list(plan["steps"])
    steps[0]["priority"] = "release-blocker"
    risks = must_dict_list(plan["risks_mitigations"])
    risks[0]["severity"] = "watch-carefully"

    normalized = normalize_plan_artifact_content(plan)

    summary = must_mapping(normalized["summary"])
    assert summary["intent_verb"] == "ship_it"
    assert summary["coverage_areas"] == ["operator-experience"]
    assert must_dict_list(summary["scope_items"])[0][
        "category"
    ] == "product-polish"
    assert must_dict_list(normalized["steps"])[0][
        "priority"
    ] == "release-blocker"
    assert must_dict_list(normalized["risks_mitigations"])[0][
        "severity"
    ] == "watch-carefully"


# ---------------------------------------------------------------------------
# 10. test_intent_verb_scope_category_add_accepts_broad_categories
# ---------------------------------------------------------------------------


def test_intent_verb_scope_category_add_accepts_broad_categories() -> None:
    """The WIDENED verb='add' mapping accepts all reasonable category values."""
    allowed = [
        "feature",
        "infra",
        "test",
        "security",
        "performance",
        "docs",
        "migration",
        "refactor",
        "cleanup",
        "file_change",
        "prompt",
        "other",
        "unknown",
    ]
    plan = _base_plan_dict()
    for category in allowed:
        summary = _cast_summary(
            plan, intent_verb="add", scope_categories=[category, "feature", "feature"]
        )
        plan["summary"] = summary
        # Should not raise
        normalize_plan_artifact_content(plan)


# ---------------------------------------------------------------------------
# 11. test_parallel_plan_and_work_units_mutually_exclusive
# ---------------------------------------------------------------------------


def test_parallel_plan_and_work_units_mutually_exclusive() -> None:
    """A plan declaring both parallel_plan and work_units is rejected."""
    plan = _base_plan_dict()
    plan["parallel_plan"] = [
        {
            "id": "unit-a",
            "description": "Parallel unit A",
            "edit_area": {"paths": ["src/a/"], "directories": []},
            "depends_on": [],
        }
    ]
    plan["work_units"] = [
        {
            "unit_id": "wu-1",
            "description": "Work unit one",
            "allowed_directories": ["src/a/"],
            "dependencies": [],
        }
    ]
    with pytest.raises(
        PlanArtifactValidationError,
        match="plan cannot declare both parallel_plan and work_units",
    ):
        normalize_plan_artifact_content(plan)


@pytest.mark.parametrize(
    ("work_units", "message"),
    [
        (
            [
                {"unit_id": "alpha", "description": "First"},
                {"unit_id": "alpha", "description": "Second"},
            ],
            "duplicate work unit ID 'alpha'",
        ),
        (
            [
                {
                    "unit_id": "alpha",
                    "description": "First",
                    "dependencies": ["missing"],
                }
            ],
            "references unknown dependency 'missing'",
        ),
        (
            [
                {
                    "unit_id": "alpha",
                    "description": "First",
                    "dependencies": ["beta"],
                },
                {
                    "unit_id": "beta",
                    "description": "Second",
                    "dependencies": ["alpha"],
                },
            ],
            "work unit dependency cycle",
        ),
        (
            [
                {
                    "unit_id": "alpha",
                    "description": "First",
                    "step_ids": ["S-99"],
                }
            ],
            "owns unknown step ID 'S-99'",
        ),
        (
            [
                {
                    "unit_id": "alpha",
                    "description": "First",
                    "step_ids": ["S-1"],
                },
                {
                    "unit_id": "beta",
                    "description": "Second",
                    "step_ids": ["S-1"],
                },
            ],
            "is owned by work units 'alpha' and 'beta'",
        ),
    ],
    ids=[
        "duplicate-id",
        "unknown-dependency",
        "dependency-cycle",
        "unknown-step",
        "duplicate-step-owner",
    ],
)
def test_canonical_work_unit_graph_and_ownership_are_strict(
    work_units: list[dict[str, object]],
    message: str,
) -> None:
    """Direct payloads retain every consumed fan-out graph invariant."""
    plan = _base_plan_dict()
    plan["work_units"] = work_units

    with pytest.raises(PlanArtifactValidationError, match=message):
        normalize_plan_artifact_content(plan)


# ---------------------------------------------------------------------------
# 12. test_verification_method_rejects_shell_invocation
# ---------------------------------------------------------------------------


def test_verification_method_rejects_shell_invocation() -> None:
    """A VerificationStep.method starting with 'bash -c ' is rejected."""
    plan = _base_plan_dict()
    plan["verification_strategy"] = [
        {"method": "bash -c rm -rf /", "expected_outcome": "nothing breaks"}
    ]
    with pytest.raises(PlanArtifactValidationError, match="must not invoke a shell interpreter"):
        normalize_plan_artifact_content(plan)


# ---------------------------------------------------------------------------
# 13. test_verification_method_allows_legitimate_bash_invocation
# ---------------------------------------------------------------------------


def test_verification_method_allows_legitimate_bash_invocation() -> None:
    """A method of 'bash ./scripts/check.sh' is allowed (prefix 'bash ' not 'bash -c ')."""
    plan = _base_plan_dict()
    plan["verification_strategy"] = [
        {"method": "bash ./scripts/check.sh", "expected_outcome": "all checks pass"}
    ]
    normalized = normalize_plan_artifact_content(plan)
    assert normalized["verification_strategy"][0]["method"] == "bash ./scripts/check.sh"


# ---------------------------------------------------------------------------
# 14. test_research_step_can_be_referenced_by_ac
# ---------------------------------------------------------------------------


def test_research_step_can_be_referenced_by_ac() -> None:
    """A valid step reference remains valid regardless of descriptive step type."""
    plan = _base_plan_dict()
    plan["steps"].append(
        {
            "number": 2,
            "title": "Investigate",
            "content": "Investigate the design.",
            "step_type": "research",
        }
    )
    plan["design"] = {
        "acceptance_criteria": {
            "criteria": [
                {"id": "AC-01", "description": "x", "satisfied_by_steps": [2]},
            ]
        }
    }
    normalized = normalize_plan_artifact_content(plan)
    design = must_mapping(normalized["design"])
    acceptance = must_mapping(design["acceptance_criteria"])
    criteria = must_dict_list(acceptance["criteria"])
    assert criteria[0]["satisfied_by_steps"] == [2]


# ---------------------------------------------------------------------------
# 15. test_verification_step_timeout_and_cwd_round_trip
# ---------------------------------------------------------------------------


def test_verification_step_timeout_and_cwd_round_trip() -> None:
    """VerificationStep round-trips timeout_seconds and cwd via exclude_defaults=True."""
    v = VerificationStep(
        method="pytest x",
        expected_outcome="pass",
        timeout_seconds=30,
        cwd="sub",
    )
    dumped = v.model_dump(exclude_defaults=True)
    assert dumped == {
        "method": "pytest x",
        "expected_outcome": "pass",
        "timeout_seconds": 30,
        "cwd": "sub",
    }


def test_format_doc_includes_new_sections() -> None:
    """The bundled format_docs/plan.md teaches the markdown plan grammar surfaces."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    for needle in (
        "## Steps",
        "S-1",
        "Depends on:",
        "Verify:",
        "Timeout:",
        "Evidence:",
        "ralph_stage_md_artifact",
        "must not start with `bash -c`, `sh -c`, or `eval`",
    ):
        assert needle in doc, f"format doc missing {needle!r}"
    normalized = " ".join(doc.split())
    assert "ralph_edit_md_plan_step" not in normalized
    assert "Resubmit the whole document" not in normalized
