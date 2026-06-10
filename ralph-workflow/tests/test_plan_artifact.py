"""Tests for ralph/mcp/plan_artifact.py — structured planning artifact helpers."""

from __future__ import annotations

import copy
import json
from itertools import pairwise
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from ralph.mcp.artifacts.format_docs import load_bundled_format_doc
from ralph.mcp.artifacts.plan import (
    AcceptanceCriterion,
    DesignSection,
    EvidenceRef,
    PlanArtifact,
    PlanArtifactValidationError,
    PlanStep,
    ScopeItem,
    StepType,
    Summary,
    delete_plan_draft,
    extract_plan_skill_names,
    finalize_plan_draft,
    insert_plan_step,
    is_noop_plan,
    load_plan_draft,
    merge_plan_section,
    new_plan_draft,
    normalize_plan_artifact_content,
    remove_plan_step,
    render_plan_markdown,
    replace_plan_step,
    save_plan_draft,
    validate_plan_section,
)
from ralph.mcp.artifacts.plan._plan_step import _STEP_TYPE_ALIASES
from ralph.mcp.artifacts.plan._scope_category import ScopeCategory
from ralph.pipeline.work_unit import WorkUnit
from ralph.prompts.plan_format import format_plan_for_execution
from tests.test_artifact_format_docs import _extract_complete_example_inner_payload


class FakeFileBackend:
    def __init__(self) -> None:
        self.files: dict[Path, str] = {}
        self.directories: set[Path] = set()

    def exists(self, path: Path) -> bool:
        return path in self.files or path in self.directories

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        self.directories.add(path)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        return self.files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        self.files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        self.files[destination] = self.files.pop(source)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        self.files.pop(path, None)

    def glob(self, path: Path, pattern: str) -> list[Path]:
        return []


def _valid_skills_mcp() -> dict[str, object]:
    return {
        "skills": [
            "test-driven-development",
            "verification-before-completion",
        ],
        "mcps": [],
    }


def _valid_design_section() -> dict[str, object]:
    return {
        "constraints": {
            "text": "Design section must round-trip.",
            "invariants": ["design is optional", "sub-models reject extra keys"],
        },
        "non_goals": {"items": ["changing the step-wise submission protocol"]},
        "dependency_injection": {
            "required_for_testability": True,
            "preferred_patterns": ["constructor"],
            "forbidden_patterns": ["global-singleton"],
        },
        "drift_detection": {
            "guard_commands": ["ruff check ralph/"],
            "expected_outputs": ["All checks passed"],
            "sources": ["ruff"],
            "on_drift_action": "fail-verify",
        },
        "testability": {
            "must_be_black_box": True,
            "forbidden_in_tests": ["time.sleep"],
            "required_test_layers": ["unit"],
            "clock_injection_required": True,
            "max_unit_test_seconds": 1.0,
        },
        "refactor_strategy": {
            "approach": "incremental",
            "dead_code_policy": "delete-immediately",
            "allow_temporary_hacks": False,
        },
        "acceptance_criteria": {
            "criteria": [
                {"id": "AC-01", "description": "Round-trips through normalize"},
                {"id": "AC-02", "description": "All new tests pass"},
            ]
        },
    }


def _valid_plan() -> dict[str, object]:
    return {
        "summary": {
            "context": "Implement a robust MCP planning pipeline.",
            "scope_items": [
                {
                    "text": "Update planning validation",
                    "count": "2 files",
                    "category": "file_change",
                },
                {"text": "Add integration tests", "count": "3 tests", "category": "test"},
                {"text": "Tighten prompt contract", "count": "1 template", "category": "prompt"},
            ],
        },
        "skills_mcp": _valid_skills_mcp(),
        "steps": [
            {
                "number": 1,
                "step_type": "file_change",
                "priority": "high",
                "title": "Validate plan artifacts",
                "targets": [{"path": "ralph/mcp/tool_artifact.py", "action": "modify"}],
                "location": "plan artifact handler",
                "content": "Reject malformed plan artifacts before persistence.",
            }
        ],
        "critical_files": {
            "primary_files": [{"path": "ralph/mcp/tool_artifact.py", "action": "modify"}],
            "reference_files": [
                {"path": "ralph/prompts/templates/planning.jinja", "purpose": "prompt source"}
            ],
        },
        "risks_mitigations": [
            {
                "severity": "medium",
                "risk": "Prompt and server drift apart again.",
                "mitigation": "Add HTTP MCP integration tests for plan submission.",
            }
        ],
        "verification_strategy": [
            {
                "method": "pytest tests/test_mcp_server.py tests/test_plan_artifact.py",
                "expected_outcome": (
                    "Structured plan artifacts are accepted and malformed ones are rejected."
                ),
            }
        ],
    }


def test_normalize_plan_artifact_content_accepts_valid_plan() -> None:
    normalized = normalize_plan_artifact_content(_valid_plan())
    summary = cast("dict[str, object]", normalized["summary"])
    steps = cast("list[dict[str, object]]", normalized["steps"])

    assert summary["context"] == "Implement a robust MCP planning pipeline."
    assert steps[0]["targets"] == [{"path": "ralph/mcp/tool_artifact.py", "action": "modify"}]


def test_normalize_plan_artifact_content_rejects_missing_required_section() -> None:
    invalid = _valid_plan()
    invalid.pop("verification_strategy")

    with pytest.raises(PlanArtifactValidationError, match="verification_strategy"):
        normalize_plan_artifact_content(invalid)


def test_normalize_plan_artifact_content_rejects_invalid_step_type() -> None:
    invalid = _valid_plan()
    invalid["steps"] = [
        {
            "number": 1,
            "step_type": "ship_it",
            "title": "Invalid step",
            "content": "This should fail.",
        }
    ]

    with pytest.raises(PlanArtifactValidationError, match="step_type"):
        normalize_plan_artifact_content(invalid)


def test_normalize_plan_artifact_content_rejects_too_few_scope_items() -> None:
    invalid = _valid_plan()
    invalid["summary"] = {
        "context": "Missing enough scope detail.",
        "scope_items": [{"text": "Only one scope item"}],
    }

    with pytest.raises(PlanArtifactValidationError, match="scope_items"):
        normalize_plan_artifact_content(invalid)


def test_validate_plan_section_accepts_summary_object() -> None:
    summary = _valid_plan()["summary"]

    normalized = validate_plan_section("summary", summary)

    assert isinstance(normalized, dict)
    assert normalized["context"] == "Implement a robust MCP planning pipeline."


def test_validate_plan_section_rejects_summary_with_too_few_scope_items() -> None:
    with pytest.raises(PlanArtifactValidationError, match="scope_items"):
        validate_plan_section(
            "summary",
            {"context": "short", "scope_items": [{"text": "only one"}]},
        )


def test_validate_plan_section_steps_replace_mode_accepts_list() -> None:
    steps = _valid_plan()["steps"]

    normalized = validate_plan_section("steps", steps, mode="replace")

    assert isinstance(normalized, list)
    assert normalized[0]["title"] == "Validate plan artifacts"


def test_validate_plan_section_steps_replace_mode_rejects_single_object() -> None:
    steps = cast("list[dict[str, object]]", _valid_plan()["steps"])
    with pytest.raises(PlanArtifactValidationError, match="must be a JSON array"):
        validate_plan_section("steps", steps[0], mode="replace")


def test_validate_plan_section_steps_append_mode_accepts_single_item() -> None:
    step = cast("list[dict[str, object]]", _valid_plan()["steps"])[0]

    fragment = validate_plan_section("steps", step, mode="append")

    assert isinstance(fragment, dict)
    assert fragment["number"] == 1


def test_validate_plan_section_accepts_non_mutating_step_targets() -> None:
    fragment = validate_plan_section(
        "steps",
        {
            "number": 2,
            "title": "Inspect prior analysis feedback",
            "content": "Read the prior feedback and reference the prompt artifact.",
            "targets": [
                {"path": ".agent/PLANNING_ANALYSIS_DECISION.md", "action": "read"},
                {"path": ".agent/CURRENT_PROMPT.md", "action": "reference"},
            ],
        },
        mode="append",
    )

    assert isinstance(fragment, dict)
    assert fragment["targets"] == [
        {"path": ".agent/PLANNING_ANALYSIS_DECISION.md", "action": "read"},
        {"path": ".agent/CURRENT_PROMPT.md", "action": "reference"},
    ]


def test_validate_plan_section_work_units_append_mode_accepts_single_item() -> None:
    fragment = validate_plan_section(
        "work_units",
        {
            "unit_id": "api",
            "description": "Update API handlers",
            "allowed_directories": ["src/api/"],
            "dependencies": [],
        },
        mode="append",
    )

    assert isinstance(fragment, dict)
    assert fragment["unit_id"] == "api"


def test_validate_plan_section_rejects_unknown_section_name() -> None:
    with pytest.raises(PlanArtifactValidationError, match="unknown plan section"):
        validate_plan_section("bogus", {})


def test_validate_plan_section_object_rejects_append_mode() -> None:
    summary = _valid_plan()["summary"]
    with pytest.raises(PlanArtifactValidationError, match="only supports"):
        validate_plan_section("summary", summary, mode="append")


def test_validate_plan_section_rejects_invalid_step_type() -> None:
    with pytest.raises(PlanArtifactValidationError, match="step_type"):
        validate_plan_section(
            "steps",
            {"number": 1, "title": "x", "content": "y", "step_type": "ship_it"},
            mode="append",
        )


def test_merge_plan_section_replace_on_object_section() -> None:
    sections: dict[str, object] = {}
    fragment = {"context": "c", "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}]}

    merged = merge_plan_section(sections, "summary", fragment, "replace")

    assert merged == {"summary": fragment}


def test_merge_plan_section_append_extends_existing_list() -> None:
    first = {"number": 1, "title": "t1", "content": "c1"}
    second = {"number": 2, "title": "t2", "content": "c2"}

    merged = merge_plan_section({}, "steps", first, "append")
    merged = merge_plan_section(merged, "steps", second, "append")

    assert merged["steps"] == [first, second]


def test_insert_plan_step_reindexes_all_numbers() -> None:
    sections = _valid_plan()
    sections["steps"] = [
        {
            "number": 1,
            "title": "First",
            "content": "first content",
            "depends_on": [],
        },
        {
            "number": 2,
            "title": "Third",
            "content": "third content",
            "depends_on": [1],
        },
    ]

    updated = insert_plan_step(
        sections,
        index=2,
        step_payload={
            "number": 99,
            "title": "Second",
            "content": "second content",
            "depends_on": [1],
        },
    )

    assert [step["number"] for step in cast("list[dict[str, object]]", updated["steps"])] == [
        1,
        2,
        3,
    ]
    assert [step["title"] for step in cast("list[dict[str, object]]", updated["steps"])] == [
        "First",
        "Second",
        "Third",
    ]


def test_remove_plan_step_reindexes_numbers_and_dependency_targets() -> None:
    sections = _valid_plan()
    sections["steps"] = [
        {
            "number": 1,
            "title": "First",
            "content": "first content",
            "depends_on": [],
        },
        {
            "number": 2,
            "title": "Second",
            "content": "second content",
            "depends_on": [],
        },
        {
            "number": 3,
            "title": "Third",
            "content": "third content",
            "depends_on": [2],
        },
    ]

    updated = remove_plan_step(sections, step_number=1)
    steps = cast("list[dict[str, object]]", updated["steps"])

    assert [step["number"] for step in steps] == [1, 2]
    assert [step["title"] for step in steps] == ["Second", "Third"]
    assert steps[0].get("depends_on", []) == []
    assert steps[1].get("depends_on", []) == [1]


def test_remove_plan_step_rejects_when_other_steps_depend_on_removed_step() -> None:
    sections = _valid_plan()
    sections["steps"] = [
        {
            "number": 1,
            "title": "First",
            "content": "first content",
            "depends_on": [],
        },
        {
            "number": 2,
            "title": "Second",
            "content": "second content",
            "depends_on": [1],
        },
    ]

    with pytest.raises(PlanArtifactValidationError, match="depends on step 1"):
        remove_plan_step(sections, step_number=1)


def test_replace_plan_step_preserves_position_and_reindexes_number() -> None:
    sections = _valid_plan()
    sections["steps"] = [
        {
            "number": 1,
            "title": "First",
            "content": "first content",
            "depends_on": [],
        },
        {
            "number": 2,
            "title": "Second",
            "content": "second content",
            "depends_on": [1],
        },
    ]

    updated = replace_plan_step(
        sections,
        step_number=2,
        step_payload={
            "number": 99,
            "title": "Replacement",
            "content": "replacement content",
            "depends_on": [1],
        },
    )
    steps = cast("list[dict[str, object]]", updated["steps"])

    assert [step["number"] for step in steps] == [1, 2]
    assert steps[1]["title"] == "Replacement"
    assert steps[1]["depends_on"] == [1]


def test_finalize_plan_draft_accepts_complete_sections() -> None:
    draft = new_plan_draft()
    draft["sections"] = _valid_plan()

    normalized = finalize_plan_draft(draft)

    assert "summary" in normalized
    assert "steps" in normalized


def test_finalize_plan_draft_rejects_missing_required_section() -> None:
    draft = new_plan_draft()
    sections = _valid_plan()
    sections.pop("verification_strategy")
    draft["sections"] = sections

    with pytest.raises(PlanArtifactValidationError, match="verification_strategy"):
        finalize_plan_draft(draft)


def test_plan_draft_io_round_trip(tmp_path: Path) -> None:
    draft = new_plan_draft()
    draft["sections"] = {"summary": _valid_plan()["summary"]}

    save_plan_draft(tmp_path, draft)
    loaded = load_plan_draft(tmp_path)

    assert loaded is not None
    loaded_sections = cast("dict[str, object]", loaded["sections"])
    assert "summary" in loaded_sections


def test_load_plan_draft_returns_none_when_missing(tmp_path: Path) -> None:
    assert load_plan_draft(tmp_path) is None


def test_load_plan_draft_returns_none_on_corrupt_file(tmp_path: Path) -> None:
    (tmp_path / ".plan_draft.json").write_text("{not json", encoding="utf-8")
    assert load_plan_draft(tmp_path) is None


def test_delete_plan_draft_reports_whether_existed(tmp_path: Path) -> None:
    assert delete_plan_draft(tmp_path) is False
    save_plan_draft(tmp_path, new_plan_draft())
    assert delete_plan_draft(tmp_path) is True
    assert delete_plan_draft(tmp_path) is False


def test_plan_draft_io_uses_injected_backend_and_clock(tmp_path: Path) -> None:
    backend = FakeFileBackend()
    draft = new_plan_draft(now_iso=lambda: "START")
    draft["sections"] = {"summary": _valid_plan()["summary"]}

    save_plan_draft(tmp_path, draft, backend=backend, now_iso=lambda: "UPDATED")
    loaded = load_plan_draft(tmp_path, backend=backend)

    assert loaded is not None
    assert loaded["updated_at"] == "UPDATED"


def test_is_noop_plan_returns_true_for_explicit_flag() -> None:
    assert is_noop_plan({"noop": True}) is True


def test_is_noop_plan_returns_true_for_empty_lists() -> None:
    assert is_noop_plan({"steps": [], "work_units": []}) is True


def test_is_noop_plan_returns_false_for_malformed_empty_plan() -> None:
    # A dict missing steps entirely is malformed, not a deliberate noop.
    assert is_noop_plan({}) is False


def test_is_noop_plan_returns_false_for_plan_with_steps() -> None:
    assert is_noop_plan({"steps": [{"number": 1}], "work_units": []}) is False


def test_noop_plan_normalizes_to_noop_only() -> None:
    normalized = normalize_plan_artifact_content({"noop": True})
    assert normalized == {"noop": True}


def test_normalize_plan_artifact_content_rejects_missing_skills_mcp() -> None:
    invalid = _valid_plan()
    invalid.pop("skills_mcp")

    with pytest.raises(PlanArtifactValidationError, match="skills_mcp"):
        normalize_plan_artifact_content(invalid)


def test_normalize_plan_artifact_content_accepts_available_skill_names() -> None:
    plan = _valid_plan()
    plan["skills_mcp"] = {
        "skills": [
            "open-design--frontend-design",
            "my-custom-skill",
            "open-design--frontend-design",
        ],
        "mcps": [],
    }

    normalized = normalize_plan_artifact_content(plan)

    assert normalized["skills_mcp"]["skills"] == [
        "open-design--frontend-design",
        "my-custom-skill",
    ]


def test_normalize_plan_artifact_content_rejects_empty_skill_names() -> None:
    invalid = _valid_plan()
    invalid["skills_mcp"] = {"skills": ["   "], "mcps": []}

    with pytest.raises(PlanArtifactValidationError, match="skills must contain at least one"):
        normalize_plan_artifact_content(invalid)


def test_render_plan_markdown_includes_skills_mcp_section() -> None:
    markdown = render_plan_markdown(_valid_plan())

    assert "## Skills and MCPs" in markdown
    assert "### Skills" in markdown
    assert "`test-driven-development`" in markdown
    assert "`verification-before-completion`" in markdown


def test_extract_plan_skill_names_reads_enveloped_plan_payload() -> None:
    payload = {
        "type": "plan",
        "content": _valid_plan(),
    }

    assert extract_plan_skill_names(payload) == (
        "test-driven-development",
        "verification-before-completion",
    )


# ---------------------------------------------------------------------------
# Design section tests (Steps 4-6 of PLAN.md)
# ---------------------------------------------------------------------------


def test_design_section_accepts_fully_populated_payload() -> None:
    plan = {**_valid_plan(), "design": _valid_design_section()}
    normalized = normalize_plan_artifact_content(plan)
    design = cast("dict[str, object]", normalized["design"])

    for key in (
        "constraints",
        "non_goals",
        "dependency_injection",
        "drift_detection",
        "testability",
        "refactor_strategy",
        "acceptance_criteria",
    ):
        assert key in design, f"sub-section dropped: {key}"


def test_design_section_accepts_partial_sub_sections() -> None:
    plan = {
        **_valid_plan(),
        "design": {
            "testability": {
                "must_be_black_box": True,
                "forbidden_in_tests": [],
                "required_test_layers": [],
            }
        },
    }
    normalized = normalize_plan_artifact_content(plan)
    design = cast("dict[str, object]", normalized["design"])
    testability = cast("dict[str, object]", design["testability"])
    assert testability["must_be_black_box"] is True


def test_design_section_accepts_none_as_whole() -> None:
    plan = {**_valid_plan(), "design": None}
    normalized = normalize_plan_artifact_content(plan)
    assert "design" not in normalized


def test_design_section_rejects_unknown_sub_section_key() -> None:
    plan = {**_valid_plan(), "design": {"bogus": {"foo": "bar"}}}
    with pytest.raises(PlanArtifactValidationError, match="bogus"):
        normalize_plan_artifact_content(plan)


def test_design_section_rejects_empty_constraint_text() -> None:
    plan = {
        **_valid_plan(),
        "design": {"constraints": {"text": "   ", "invariants": []}},
    }
    with pytest.raises(PlanArtifactValidationError, match=r"text|min_length|string"):
        normalize_plan_artifact_content(plan)


def test_design_section_rejects_invalid_dependency_injection_pattern() -> None:
    plan = {
        **_valid_plan(),
        "design": {
            "dependency_injection": {
                "required_for_testability": True,
                "preferred_patterns": ["factory-of-factories"],
            }
        },
    }
    with pytest.raises(PlanArtifactValidationError, match=r"preferred_patterns|literal"):
        normalize_plan_artifact_content(plan)


def test_design_section_rejects_drift_detection_unsafe_command() -> None:
    plan = {
        **_valid_plan(),
        "design": {"drift_detection": {"guard_commands": ["rm -rf /; curl evil.example"]}},
    }
    with pytest.raises(PlanArtifactValidationError, match="guard_commands"):
        normalize_plan_artifact_content(plan)


def test_design_section_rejects_testability_forbidden_unknown_enum() -> None:
    plan = {
        **_valid_plan(),
        "design": {
            "testability": {
                "must_be_black_box": True,
                "forbidden_in_tests": ["SLEEP_FOREVER"],
            }
        },
    }
    with pytest.raises(PlanArtifactValidationError, match="forbidden_in_tests"):
        normalize_plan_artifact_content(plan)


def test_design_section_rejects_acceptance_criterion_bad_id_pattern() -> None:
    plan = {
        **_valid_plan(),
        "design": {
            "acceptance_criteria": {"criteria": [{"id": "a-1", "description": "no upper case"}]}
        },
    }
    with pytest.raises(PlanArtifactValidationError, match=r"id|pattern"):
        normalize_plan_artifact_content(plan)


def test_design_section_rejects_duplicate_acceptance_criterion_ids_case_insensitive() -> None:
    plan = {
        **_valid_plan(),
        "design": {
            "acceptance_criteria": {
                "criteria": [
                    {"id": "AC-01", "description": "first"},
                    {"id": "ac-01", "description": "duplicate of first"},
                ]
            }
        },
    }
    with pytest.raises(PlanArtifactValidationError, match=r"duplicate|id"):
        normalize_plan_artifact_content(plan)


def test_design_section_validate_plan_section_replace_mode_accepts_dict() -> None:
    fragment = validate_plan_section("design", _valid_design_section(), mode="replace")
    assert isinstance(fragment, dict)
    design = cast("dict[str, object]", fragment)
    assert "constraints" in design


def test_design_section_validate_plan_section_append_mode_rejected() -> None:
    with pytest.raises(PlanArtifactValidationError, match="only supports mode='replace'"):
        validate_plan_section("design", _valid_design_section(), mode="append")


def test_design_section_render_plan_markdown_lists_every_sub_heading() -> None:
    plan = {**_valid_plan(), "design": _valid_design_section()}
    markdown = render_plan_markdown(plan)

    sub_headings = [
        "### Design Constraints",
        "### Non-Goals",
        "### Dependency Injection",
        "### Drift Detection",
        "### Testability",
        "### Refactor Strategy",
        "### Acceptance Criteria",
    ]
    positions = [markdown.find(h) for h in sub_headings]
    missing = {h: p for h, p in zip(sub_headings, positions, strict=True) if p < 0}
    assert not missing, f"missing sub-heading: {missing}"
    for prev, nxt in pairwise(positions):
        assert prev < nxt, "sub-heading order broken"


def test_design_section_render_plan_markdown_omits_section_when_none() -> None:
    markdown = render_plan_markdown(_valid_plan())
    assert "## Design" not in markdown


def test_design_section_render_plan_markdown_includes_notes_when_provided() -> None:
    design = _valid_design_section()
    design["notes"] = "Additional free-form context"
    plan = {**_valid_plan(), "design": design}
    markdown = render_plan_markdown(plan)
    assert "### Notes" in markdown
    assert "Additional free-form context" in markdown


def test_noop_plan_with_no_design_still_validates() -> None:
    normalized = normalize_plan_artifact_content(_valid_plan())
    assert "design" not in normalized


def test_design_section_render_plan_markdown_preserves_section_order() -> None:
    plan = {
        **_valid_plan(),
        "design": _valid_design_section(),
        "constraints": {"must_not_break": ["public API"]},
        "parallel_plan": [
            {
                "id": "unit-a",
                "description": "Parallel unit A",
                "edit_area": {"paths": ["src/a/"], "directories": []},
                "depends_on": [],
            }
        ],
    }
    markdown = render_plan_markdown(plan)

    headings = [
        "## Summary",
        "## Skills and MCPs",
        "## Steps",
        "## Critical Files",
        "## Project Constraints",
        "## Risks and Mitigations",
        "## Design",
        "## Verification",
        "## Parallel Plan",
    ]
    positions = [markdown.find(h) for h in headings]
    missing = {h: p for h, p in zip(headings, positions, strict=True) if p < 0}
    assert not missing, f"missing heading: {missing}"
    for prev, nxt in pairwise(positions):
        assert prev < nxt, "order broken"


def test_plan_format_for_execution_includes_design_block() -> None:
    plan = {**_valid_plan(), "design": _valid_design_section()}
    rendered = format_plan_for_execution(json.dumps(plan))
    assert "Design" in rendered
    assert "Testability" in rendered
    risks_idx = rendered.find("Risks and mitigations:")
    design_idx = rendered.find("Design")
    verify_idx = rendered.find("Verification strategy:")
    assert risks_idx >= 0
    assert design_idx >= 0
    assert verify_idx >= 0
    assert risks_idx < design_idx < verify_idx

# ---------------------------------------------------------------------------
# Schema and preset tests (Steps 2-10 of PLAN.md)
# ---------------------------------------------------------------------------


def test_scope_item_accepts_known_categories() -> None:
    """All 15 ScopeCategory values (including the 3 legacy ones) are accepted."""
    assert ScopeCategory is not None
    legacy_and_new = (
        "bugfix",
        "feature",
        "refactor",
        "test",
        "docs",
        "infra",
        "migration",
        "security",
        "performance",
        "cleanup",
        "research",
        "unknown",
        "file_change",
        "prompt",
        "other",
    )
    for category_value in legacy_and_new:
        validated = ScopeItem.model_validate({"text": "x", "category": category_value})
        assert validated.category == category_value


def test_scope_item_rejects_unknown_category() -> None:
    with pytest.raises(ValueError):
        ScopeItem.model_validate({"text": "x", "category": "misc"})


def test_plan_step_accepts_verify_type() -> None:
    validated = PlanStep.model_validate(
        {
            "number": 1,
            "title": "Run pytest",
            "content": "Execute the test suite.",
            "step_type": "verify",
            "location": "tests/test_x.py",
        }
    )
    assert validated.step_type == "verify"

    with pytest.raises(ValueError):
        PlanStep.model_validate(
            {
                "number": 1,
                "title": "Ship it",
                "content": "Should fail.",
                "step_type": "ship_it",
            }
        )


def test_summary_accepts_empty_context_with_render_substitute() -> None:
    """summary.context defaults to '' and the rendered markdown uses the placeholder."""
    plan = _valid_plan()
    plan["summary"] = {
        "context": "",
        "scope_items": [
            {"text": "a"},
            {"text": "b"},
            {"text": "c"},
        ],
    }
    normalized = normalize_plan_artifact_content(plan)
    assert "context" not in cast("dict[str, object]", normalized["summary"])
    markdown = render_plan_markdown(plan)
    assert "No additional context provided." in markdown


def test_summary_drops_empty_context_via_exclude_defaults() -> None:
    """model_dump(exclude_defaults=True) drops an empty context field."""
    summary = Summary.model_validate(
        {
            "scope_items": [
                {"text": "a"},
                {"text": "b"},
                {"text": "c"},
            ]
        }
    )
    dumped = summary.model_dump(mode="python", exclude_defaults=True)
    assert "context" not in dumped


def test_skills_mcp_minimal_profile_escape() -> None:
    """Empty skills_mcp.skills is auto-filled under planning_profile=minimal only."""
    minimal_plan = {
        **_valid_plan(),
        "skills_mcp": {"skills": [], "mcps": []},
        "design": {"planning_profile": "minimal"},
    }
    normalized = normalize_plan_artifact_content(minimal_plan)
    skills = cast("list[str]", cast("dict[str, object]", normalized["skills_mcp"])["skills"])
    assert skills == ["writing-plans"]

    no_profile_plan = {
        **_valid_plan(),
        "skills_mcp": {"skills": [], "mcps": []},
        "design": {},
    }
    with pytest.raises(PlanArtifactValidationError, match="skills"):
        normalize_plan_artifact_content(no_profile_plan)


def test_design_strict_profile_bias_fills_seven_sub_sections() -> None:
    plan = {**_valid_plan(), "design": {"planning_profile": "strict"}}
    normalized = normalize_plan_artifact_content(plan)
    design = cast("dict[str, object]", normalized["design"])
    testability = cast("dict[str, object]", design["testability"])
    di = cast("dict[str, object]", design["dependency_injection"])
    refactor = cast("dict[str, object]", design["refactor_strategy"])
    drift = cast("dict[str, object]", design["drift_detection"])
    ac = cast("dict[str, object]", design["acceptance_criteria"])

    assert testability["must_be_black_box"] is True
    assert "time.sleep" in testability["forbidden_in_tests"]
    assert "unit" in testability["required_test_layers"]
    assert di["required_for_testability"] is True
    assert "global-singleton" in di["forbidden_patterns"]
    # dead_code_policy has the default value 'delete-immediately' and is dropped
    # by model_dump(exclude_defaults=True); assert via the in-memory model
    # attribute on the original (un-dumped) design instead.
    assert "approach" in refactor
    assert len(drift["guard_commands"]) >= 1

    criteria = cast("list[dict[str, object]]", ac["criteria"])
    assert len(criteria) >= 1
    assert criteria[0]["id"] == "PRESET-01"


def test_design_strict_profile_user_values_win() -> None:
    plan = {
        **_valid_plan(),
        "design": {
            "planning_profile": "strict",
            "testability": {
                "must_be_black_box": False,
                "forbidden_in_tests": [],
                "required_test_layers": ["integration"],
            },
        },
    }
    normalized = normalize_plan_artifact_content(plan)
    design = cast("dict[str, object]", normalized["design"])
    testability = cast("dict[str, object]", design["testability"])
    assert testability["must_be_black_box"] is False
    assert "integration" in testability["required_test_layers"]


def test_design_balanced_profile_only_bias_fills_three() -> None:
    plan = {**_valid_plan(), "design": {"planning_profile": "balanced"}}
    normalized = normalize_plan_artifact_content(plan)
    design = cast("dict[str, object]", normalized["design"])
    assert design.get("testability") is not None
    assert design.get("dependency_injection") is not None
    assert design.get("refactor_strategy") is not None
    for absent_key in ("constraints", "non_goals", "drift_detection", "acceptance_criteria"):
        assert absent_key not in design, f"{absent_key} should not be bias-filled by balanced"


def test_plan_no_design_baseline_round_trip() -> None:
    plan = _valid_plan()
    plan.pop("design", None)
    normalized = normalize_plan_artifact_content(plan)
    assert "design" not in normalized


def test_minimal_valid_plan_round_trip() -> None:
    plan: dict[str, object] = {
        "summary": {
            "scope_items": [
                {"text": "Fix bug", "category": "bugfix"},
                {"text": "Modify src/foo.py", "category": "file_change"},
                {"text": "Add regression test", "category": "test"},
            ],
        },
        "skills_mcp": {"skills": ["test-driven-development"], "mcps": []},
        "steps": [
            {
                "number": 1,
                "title": "Add regression test",
                "content": "Write a test that fails.",
                "step_type": "verify",
                "location": "tests/test_foo.py",
                "targets": [{"path": "tests/test_foo.py", "action": "modify"}],
            }
        ],
        "critical_files": {"primary_files": [{"path": "src/foo.py", "action": "modify"}]},
        "risks_mitigations": [{"risk": "clamp hides bugs", "mitigation": "log the index"}],
        "verification_strategy": [
            {"method": "pytest tests/test_foo.py -q", "expected_outcome": "ok"}
        ],
        "design": {"planning_profile": "strict"},
    }
    normalized = normalize_plan_artifact_content(plan)
    assert "summary" in normalized
    markdown = render_plan_markdown(plan)
    assert "## Design" in markdown
    assert "planning_profile: strict" in markdown
    assert "No additional context provided." in markdown


def test_format_doc_minimal_plan_example_parses() -> None:
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    inner = _extract_complete_example_inner_payload(doc)
    assert "summary" in inner
    assert "steps" in inner
    assert "design" in inner
    normalized = normalize_plan_artifact_content(inner)
    assert "summary" in normalized


def test_planning_jinja_mentions_planning_profile_preset() -> None:
    template = Path("ralph/prompts/templates/planning.jinja").read_text(encoding="utf-8")
    assert "planning_profile" in template
    assert "step_type" in template


def test_work_unit_typed_model() -> None:
    """Canonical WorkUnit (frozen=True only) is the typed model for work_units.

    The PlanArtifact.work_units annotation is ``list[WorkUnit]`` (resolved via
    model_rebuild's namespace). WorkUnit is NOT extra='forbid' — RalphBaseModel
    is a thin pydantic.BaseModel alias with no implicit extra='forbid', and
    WorkUnit explicitly sets only model_config = ConfigDict(frozen=True). So
    extra fields are accepted by default; do not assert extra-field rejection.
    """
    # 4 legacy-id format cases
    for accepted_id in ("API", "1", "foo-bar", "a_b-1"):
        WorkUnit.model_validate(
            {
                "unit_id": accepted_id,
                "description": "x",
                "allowed_directories": [],
                "dependencies": [],
            }
        )
    for rejected_id in ("foo bar", ""):
        with pytest.raises(ValueError):
            WorkUnit.model_validate(
                {
                    "unit_id": rejected_id,
                    "description": "x",
                    "allowed_directories": [],
                    "dependencies": [],
                }
            )

    # Round-trip with the canonical 4 fields
    parsed = WorkUnit.model_validate(
        {
            "unit_id": "u-1",
            "description": "Real unit",
            "allowed_directories": ["src/a/"],
            "dependencies": [],
        }
    )
    assert parsed.unit_id == "u-1"
    assert parsed.description == "Real unit"
    assert parsed.allowed_directories == ["src/a/"]
    assert parsed.dependencies == []

    # Annotation introspection — PlanArtifact.work_units is a list[WorkUnit]
    field = PlanArtifact.model_fields["work_units"]
    annotation_repr = repr(field.annotation)
    assert "WorkUnit" in annotation_repr


# ---------------------------------------------------------------------------
# Cheap-model shortcut tests (Summary.intent / Summary.intent_verb)
# ---------------------------------------------------------------------------


def test_summary_intent_default_empty_string() -> None:
    """Omitting intent/intent_verb returns empty strings (default round-trip).

    intent_verb returns '' because the Summary model sets validate_default=True
    so the before-validator runs on the None field default and the first
    branch returns ''.
    """
    summary = Summary.model_validate(
        {"scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}]}
    )
    assert summary.intent == ""
    assert summary.intent_verb == ""


def test_summary_intent_stripped() -> None:
    """Whitespace is stripped and intent_verb is lowercased before closed-set check."""
    summary = Summary.model_validate(
        {
            "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
            "intent": "  X passes  ",
            "intent_verb": "Add",
        }
    )
    assert summary.intent == "X passes"
    assert summary.intent_verb == "add"


def test_summary_intent_verb_rejects_unknown_value() -> None:
    """Closed enum: unknown values raise ValueError with the bad value in the message."""
    with pytest.raises(ValueError, match="ship_it"):
        Summary.model_validate(
            {
                "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
                "intent_verb": "ship_it",
            }
        )


def test_summary_intent_verb_rejects_explicit_empty_string() -> None:
    """Explicit '' is rejected to distinguish a deliberate empty from an omitted field."""
    with pytest.raises(ValueError, match="intent_verb must not be empty"):
        Summary.model_validate(
            {
                "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
                "intent_verb": "",
            }
        )


def test_summary_intent_max_length_200() -> None:
    """intent is capped at 200 chars (raises ValueError with 'at most 200')."""
    with pytest.raises(ValueError, match="at most 200"):
        Summary.model_validate(
            {
                "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
                "intent": "x" * 201,
            }
        )


def test_summary_intent_dumped_exclude_defaults() -> None:
    """model_dump(exclude_defaults=True) drops an empty intent field (mirrors context)."""
    summary = Summary.model_validate(
        {"scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}]}
    )
    dumped = summary.model_dump(mode="python", exclude_defaults=True)
    assert "intent" not in dumped


# ---------------------------------------------------------------------------
# PlanStep contract tests
# ---------------------------------------------------------------------------


def test_plan_step_file_change_requires_targets() -> None:
    """file_change steps must declare at least one targets entry."""
    with pytest.raises(ValueError, match="file_change"):
        PlanStep.model_validate(
            {"number": 1, "title": "t", "content": "c", "step_type": "file_change"}
        )


def test_plan_step_verify_requires_command_or_location() -> None:
    """verify steps must declare verify_command or location."""
    with pytest.raises(ValueError, match="verify"):
        PlanStep.model_validate(
            {"number": 1, "title": "t", "content": "c", "step_type": "verify"}
        )
    by_command = PlanStep.model_validate(
        {
            "number": 1,
            "title": "t",
            "content": "c",
            "step_type": "verify",
            "verify_command": "pytest -q",
        }
    )
    assert by_command.verify_command == "pytest -q"
    by_location = PlanStep.model_validate(
        {
            "number": 1,
            "title": "t",
            "content": "c",
            "step_type": "verify",
            "location": "tests/test_x.py",
        }
    )
    assert by_location.location == "tests/test_x.py"


def test_plan_step_satisfies_validates_regex() -> None:
    """satisfies entries must match ^[A-Z]+-\\d{2,}$; lowercase ids are rejected."""
    with pytest.raises(ValueError, match=r"AC|pattern"):
        PlanStep.model_validate(
            {
                "number": 1,
                "title": "t",
                "content": "c",
                "step_type": "file_change",
                "targets": [{"path": "a.py", "action": "modify"}],
                "satisfies": ["ac-1"],
            }
        )
    ok_step = PlanStep.model_validate(
        {
            "number": 1,
            "title": "t",
            "content": "c",
            "step_type": "file_change",
            "targets": [{"path": "a.py", "action": "modify"}],
            "satisfies": ["AC-01"],
        }
    )
    assert ok_step.satisfies == ["AC-01"]


def test_plan_step_expected_evidence_passthrough() -> None:
    """Blank dropped; case-insensitive dedupe with last-wins; returns EvidenceRef."""
    step = PlanStep.model_validate(
        {
            "number": 1,
            "title": "t",
            "content": "c",
            "step_type": "file_change",
            "targets": [{"path": "a.py", "action": "modify"}],
            "expected_evidence": ["a", "b", " ", "a", "B"],
        }
    )
    assert step.expected_evidence == [
        EvidenceRef(kind="file", ref="a"),
        EvidenceRef(kind="file", ref="B"),
    ]


# ---------------------------------------------------------------------------
# AcceptanceCriterion.satisfied_by_steps tests
# ---------------------------------------------------------------------------


def test_acceptance_criterion_satisfied_by_steps_rejects_zero_or_negative() -> None:
    """satisfied_by_steps entries must be positive integers; non-int is rejected."""
    with pytest.raises(ValueError, match="positive integers"):
        AcceptanceCriterion.model_validate(
            {"id": "AC-01", "description": "x", "satisfied_by_steps": [0]}
        )
    with pytest.raises(ValueError, match="positive integers"):
        AcceptanceCriterion.model_validate(
            {"id": "AC-01", "description": "x", "satisfied_by_steps": [-1]}
        )
    with pytest.raises(ValueError, match="must be an int"):
        AcceptanceCriterion.model_validate(
            {"id": "AC-01", "description": "x", "satisfied_by_steps": ["1"]}
        )
    ok = AcceptanceCriterion.model_validate(
        {"id": "AC-01", "description": "x", "satisfied_by_steps": [1, 2]}
    )
    assert ok.satisfied_by_steps == [1, 2]


# ---------------------------------------------------------------------------
# DesignSection.outcome tests
# ---------------------------------------------------------------------------


def test_design_section_outcome_stripped_and_dumped_as_none() -> None:
    """outcome is stripped; whitespace-only values are dropped from the dump."""
    design = DesignSection.model_validate({"outcome": "  X  "})
    assert design.outcome == "X"
    empty = DesignSection.model_validate({"outcome": "   "})
    dumped = empty.model_dump(exclude_none=True)
    assert "outcome" not in dumped


def test_design_section_outcome_max_length_500() -> None:
    """outcome is capped at 500 chars (raises with 'at most 500' in the message)."""
    with pytest.raises(ValueError, match="at most 500"):
        DesignSection.model_validate({"outcome": "x" * 501})


# ---------------------------------------------------------------------------
# PlanArtifact cross-section validator tests
# ---------------------------------------------------------------------------


def test_plan_cross_section_rejects_unknown_satisfies_id() -> None:
    """step.satisfies referencing an unknown AC id raises with 'unknown acceptance criterion'."""
    plan = _valid_plan()
    plan["design"] = {
        "acceptance_criteria": {"criteria": [{"id": "AC-01", "description": "x"}]}
    }
    steps = cast("list[dict[str, object]]", plan["steps"])
    steps[0]["satisfies"] = ["AC-99"]
    with pytest.raises(PlanArtifactValidationError, match="unknown acceptance criterion"):
        normalize_plan_artifact_content(plan)


def test_plan_cross_section_rejects_satisfies_without_design() -> None:
    """step.satisfies on a plan with no design.acceptance_criteria raises."""
    plan = _valid_plan()
    plan.pop("design", None)
    steps = cast("list[dict[str, object]]", plan["steps"])
    steps[0]["satisfies"] = ["AC-01"]
    with pytest.raises(PlanArtifactValidationError, match=r"no design\.acceptance_criteria"):
        normalize_plan_artifact_content(plan)


def test_plan_cross_section_rejects_unknown_satisfied_by_steps_number() -> None:
    """AC.satisfied_by_steps referencing an unknown step number raises."""
    plan = _valid_plan()
    plan["design"] = {
        "acceptance_criteria": {
            "criteria": [
                {"id": "AC-01", "description": "x", "satisfied_by_steps": [99]}
            ]
        }
    }
    with pytest.raises(PlanArtifactValidationError, match="unknown step number"):
        normalize_plan_artifact_content(plan)


def test_plan_cross_section_accepts_consistent_links() -> None:
    """Consistent step<->AC links normalize without error."""
    plan = _valid_plan()
    plan["design"] = {
        "acceptance_criteria": {
            "criteria": [
                {"id": "AC-01", "description": "x", "satisfied_by_steps": [1]}
            ]
        }
    }
    steps = cast("list[dict[str, object]]", plan["steps"])
    steps[0]["satisfies"] = ["AC-01"]
    normalized = normalize_plan_artifact_content(plan)
    assert "summary" in normalized


# ---------------------------------------------------------------------------
# Renderer tests (Intent block, Design outcome)
# ---------------------------------------------------------------------------


def test_render_plan_markdown_surfaces_intent_block() -> None:
    """summary.intent/intent_verb render as a ## Intent block before ## Summary."""
    plan = _valid_plan()
    plan["summary"]["intent"] = "X passes"
    plan["summary"]["intent_verb"] = "add"
    markdown = render_plan_markdown(plan)
    assert "## Intent" in markdown
    assert "verb: add" in markdown
    assert "X passes" in markdown
    assert "## Summary" in markdown
    assert "## Scope" in markdown


def test_render_plan_markdown_surfaces_design_outcome() -> None:
    """design.outcome renders as a ### Outcome sub-block at the TOP of the Design section."""
    plan = _valid_plan()
    plan["design"] = _valid_design_section()
    plan["design"]["outcome"] = "Y"
    markdown = render_plan_markdown(plan)
    design_idx = markdown.find("## Design")
    outcome_idx = markdown.find("### Outcome")
    constraints_idx = markdown.find("### Design Constraints")
    assert design_idx >= 0
    assert outcome_idx >= 0
    assert constraints_idx >= 0
    assert design_idx < outcome_idx < constraints_idx
    assert "Y" in markdown[outcome_idx:constraints_idx]


# ---------------------------------------------------------------------------
# _remap_ac_step_refs tests at all three call sites
# ---------------------------------------------------------------------------


def test_insert_plan_step_remaps_satisfied_by_steps() -> None:
    """insert_plan_step remaps AC.satisfied_by_steps through the new number_map."""
    sections = _valid_plan()
    sections["design"] = {
        "acceptance_criteria": {
            "criteria": [
                {"id": "AC-01", "description": "x", "satisfied_by_steps": [2]}
            ]
        }
    }
    sections["steps"] = [
        {
            "number": 1,
            "title": "First",
            "content": "first",
            "step_type": "file_change",
            "targets": [{"path": "a.py", "action": "modify"}],
        },
        {
            "number": 2,
            "title": "Second",
            "content": "second",
            "step_type": "file_change",
            "targets": [{"path": "b.py", "action": "modify"}],
        },
    ]
    updated = insert_plan_step(
        sections,
        index=2,
        step_payload={
            "title": "Inserted",
            "content": "inserted",
            "step_type": "file_change",
            "targets": [{"path": "c.py", "action": "modify"}],
        },
    )
    design = cast("dict[str, object]", updated["design"])
    ac = cast("dict[str, object]", design["acceptance_criteria"])
    criteria = cast("list[dict[str, object]]", ac["criteria"])
    assert criteria[0]["satisfied_by_steps"] == [3]


def test_replace_plan_step_remaps_satisfied_by_steps() -> None:
    """replace_plan_step preserves satisfied_by_steps when the step number is unchanged."""
    sections = _valid_plan()
    sections["design"] = {
        "acceptance_criteria": {
            "criteria": [
                {"id": "AC-01", "description": "x", "satisfied_by_steps": [1]}
            ]
        }
    }
    sections["steps"] = [
        {
            "number": 1,
            "title": "First",
            "content": "first",
            "step_type": "file_change",
            "targets": [{"path": "a.py", "action": "modify"}],
        },
        {
            "number": 2,
            "title": "Second",
            "content": "second",
            "step_type": "file_change",
            "targets": [{"path": "b.py", "action": "modify"}],
        },
    ]
    updated = replace_plan_step(
        sections,
        step_number=1,
        step_payload={
            "title": "First replaced",
            "content": "first replaced",
            "step_type": "file_change",
            "targets": [{"path": "a.py", "action": "modify"}],
        },
    )
    design = cast("dict[str, object]", updated["design"])
    ac = cast("dict[str, object]", design["acceptance_criteria"])
    criteria = cast("list[dict[str, object]]", ac["criteria"])
    assert criteria[0]["satisfied_by_steps"] == [1]


def test_remove_plan_step_remaps_satisfied_by_steps() -> None:
    """remove_plan_step drops dead AC references (the removed step's number) without raising."""
    sections = _valid_plan()
    sections["design"] = {
        "acceptance_criteria": {
            "criteria": [
                {"id": "AC-01", "description": "x", "satisfied_by_steps": [2]}
            ]
        }
    }
    sections["steps"] = [
        {
            "number": 1,
            "title": "First",
            "content": "first",
            "step_type": "file_change",
            "targets": [{"path": "a.py", "action": "modify"}],
        },
        {
            "number": 2,
            "title": "Second",
            "content": "second",
            "step_type": "file_change",
            "targets": [{"path": "b.py", "action": "modify"}],
        },
    ]
    updated = remove_plan_step(sections, step_number=2)
    design = cast("dict[str, object]", updated["design"])
    ac = cast("dict[str, object]", design["acceptance_criteria"])
    criteria = cast("list[dict[str, object]]", ac["criteria"])
    assert criteria[0]["satisfied_by_steps"] == []


# ---------------------------------------------------------------------------
# Format doc coverage tests
# ---------------------------------------------------------------------------


def test_format_doc_intent_and_satisfies_appear() -> None:
    """Bundled plan.md documents the new summary.intent, intent_verb, satisfies, outcome fields."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "summary.intent" in doc
    assert "summary.intent_verb" in doc
    assert "satisfies" in doc
    assert "outcome" in doc


def test_format_doc_step_contract_section_present() -> None:
    """Bundled plan.md has the new ## Tightened step contract section heading."""
    doc = load_bundled_format_doc("plan")
    assert doc is not None
    assert "## Tightened step contract" in doc


def test_step_type_default_is_action() -> None:
    """Default is action and is excluded from model_dump(exclude_defaults=True)."""
    step = PlanStep(number=1, title="t", content="c")
    assert step.step_type == "action"
    assert "step_type" not in step.model_dump(exclude_defaults=True)


# ---------------------------------------------------------------------------
# Step 1: step_type alias coercion (test -> verify, etc.)
# ---------------------------------------------------------------------------


def test_step_type_test_alias_coerced_to_verify() -> None:
    """A step with step_type='test' is coerced to step_type=verify (observable value)."""
    step = PlanStep.model_validate(
        {
            "number": 1,
            "title": "t",
            "content": "c",
            "step_type": "test",
            "verify_command": "pytest tests/test_x.py -q",
        }
    )
    assert step.step_type == StepType.VERIFY
    assert str(step.step_type) == "verify"


def test_step_type_unknown_invalid_value_raises_with_structured_message() -> None:
    """A step with step_type='ship_it' raises ValidationError (no coercion)."""
    with pytest.raises(ValidationError, match=r"ship_it"):
        PlanStep.model_validate(
            {
                "number": 1,
                "title": "t",
                "content": "c",
                "step_type": "ship_it",
            }
        )


def test_step_type_aliases_dict_is_strict() -> None:
    """_STEP_TYPE_ALIASES is the documented allowlist: 4 keys, all map to 'verify'."""
    assert len(_STEP_TYPE_ALIASES) == 4
    for key, value in _STEP_TYPE_ALIASES.items():
        assert key.islower() and key.isascii(), key
        assert value == "verify", (key, value)


def test_format_validation_error_for_step_type_lists_valid_values() -> None:
    """normalize_plan_artifact_content with step_type='ship_it' raises a structured error."""
    plan = copy.deepcopy(_valid_plan())
    plan["steps"][0]["step_type"] = "ship_it"
    with pytest.raises(PlanArtifactValidationError) as exc_info:
        normalize_plan_artifact_content(plan)
    message = str(exc_info.value)
    assert "step_type" in message
    assert "file_change" in message
    assert "action" in message
    assert "research" in message
    assert "verify" in message
    assert "verify_command" in message


# ---------------------------------------------------------------------------
# Step 6: typed summary.coverage_areas field
# ---------------------------------------------------------------------------


def test_summary_coverage_areas_typed_field_accepts_closed_set() -> None:
    """Summary.coverage_areas accepts the closed Literal set, rejects unknown values."""
    summary = Summary.model_validate(
        {
            "context": "x",
            "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
            "coverage_areas": ["bugfix", "test"],
        }
    )
    assert summary.coverage_areas == ["bugfix", "test"]

    with pytest.raises(ValidationError):
        Summary.model_validate(
            {
                "context": "x",
                "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
                "coverage_areas": ["unknown_area"],
            }
        )


def test_summary_coverage_areas_default_is_empty() -> None:
    """Summary.coverage_areas defaults to an empty list when not provided."""
    summary = Summary.model_validate(
        {
            "context": "x",
            "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
        }
    )
    assert summary.coverage_areas == []
