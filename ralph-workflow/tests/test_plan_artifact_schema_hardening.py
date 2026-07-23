"""Schema-hardening tests for the plan-artifact package.

The 18 net-new tests in this module cover the four cross-section
validators added to ``PlanArtifact``, the typed ``EvidenceRef`` /
``PlanConstraints`` sub-models, the new ``timeout_seconds`` / ``cwd``
fields on ``VerificationStep``, and the renderer / format-doc
synchronization. All tests are pure Pydantic round-trips with no
``time.sleep``, no real subprocess, and no real file I/O so the
``audit_test_policy`` guard accepts them and the 60-second combined
test budget stays well within budget.
"""

from __future__ import annotations

import json

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
    render_plan_markdown,
    validate_plan_section,
)
from ralph.prompts.plan_format import format_plan_for_execution


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
# 4. test_evidence_ref_string_coercion
# ---------------------------------------------------------------------------


def test_evidence_ref_string_coercion() -> None:
    """EvidenceRef('foo.py') coerces a bare string to kind='file', ref='foo.py'.

    PA-001 regression test that the legacy string-typed fixture at
    test_plan_artifact.py:1239 continues to work via the model-level
    coercion path.
    """
    r = EvidenceRef("foo.py")
    assert r.kind == "file"
    assert r.ref == "foo.py"


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
# 9. test_intent_verb_scope_category_fix_incompatible_with_feature
# ---------------------------------------------------------------------------


def test_intent_verb_scope_category_fix_incompatible_with_feature() -> None:
    """verb='fix' rejects scope_items with category='feature'; verb='add' rejects 'bugfix'."""
    plan = _base_plan_dict()
    bad_summary = _cast_summary(
        plan, intent_verb="fix", scope_categories=["feature", "bugfix", "unknown"]
    )
    plan["summary"] = bad_summary
    with pytest.raises(PlanArtifactValidationError, match="incompatible with intent_verb='fix'"):
        normalize_plan_artifact_content(plan)

    # verb='add' rejects 'bugfix'
    bad_summary = _cast_summary(
        plan, intent_verb="add", scope_categories=["bugfix", "feature", "infra"]
    )
    plan["summary"] = bad_summary
    with pytest.raises(PlanArtifactValidationError, match="incompatible with intent_verb='add'"):
        normalize_plan_artifact_content(plan)


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
# 14. test_research_step_cannot_satisfy_ac
# ---------------------------------------------------------------------------


def test_research_step_cannot_satisfy_ac() -> None:
    """A satisfied_by_steps entry pointing at a research step is rejected."""
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
    with pytest.raises(
        PlanArtifactValidationError,
        match="satisfied_by_steps cannot reference a research or verify step",
    ):
        normalize_plan_artifact_content(plan)


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


# ---------------------------------------------------------------------------
# 16. test_renderer_includes_project_constraints_section
# ---------------------------------------------------------------------------


def test_renderer_includes_project_constraints_section() -> None:
    """render_plan_markdown emits ## Project Constraints between Critical Files and Risks."""
    plan = _base_plan_dict()
    plan["constraints"] = {"must_not_break": ["public API"]}
    markdown = render_plan_markdown(plan)
    constraints_idx = markdown.find("## Project Constraints")
    critical_idx = markdown.find("## Critical Files")
    risks_idx = markdown.find("## Risks and Mitigations")
    assert critical_idx >= 0
    assert constraints_idx >= 0
    assert risks_idx >= 0
    assert critical_idx < constraints_idx < risks_idx


# ---------------------------------------------------------------------------
# 17. test_format_plan_for_execution_surfaces_step_fields
# ---------------------------------------------------------------------------


def test_format_plan_for_execution_surfaces_step_fields() -> None:
    """format_plan_for_execution surfaces step_type, expected_evidence, verify_command."""
    plan = _base_plan_dict()
    plan["steps"][0]["step_type"] = "file_change"
    plan["steps"][0]["expected_evidence"] = [
        {"kind": "file", "ref": "src/a.py"},
        {"kind": "test_name", "ref": "tests/test_x.py::test_a"},
    ]
    plan["steps"][0]["verify_command"] = "pytest tests/test_x.py -q"
    plan["constraints"] = {"must_not_break": ["public API"]}
    rendered = format_plan_for_execution(json.dumps(plan))
    assert "step_type: file_change" in rendered
    assert "file: src/a.py" in rendered
    assert "test_name: tests/test_x.py::test_a" in rendered
    assert "verify_command: `pytest tests/test_x.py -q`" in rendered
    assert "Project Constraints:" in rendered


# ---------------------------------------------------------------------------
# 18. test_format_doc_includes_new_sections
# ---------------------------------------------------------------------------


def test_format_doc_includes_new_sections() -> None:
    """The bundled format_docs/plan.md teaches the markdown plan grammar surfaces."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    for needle in (
        "## Steps",
        "S-1",
        "depends_on",
        "verify_command",
        "timeout_seconds",
        "expected_evidence",
        "ralph_edit_md_plan_step",
        "shell-invocation guard",
    ):
        assert needle in doc, f"format doc missing {needle!r}"
