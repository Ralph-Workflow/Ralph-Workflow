"""Tests for ralph/mcp/plan_artifact.py — structured planning artifact helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from ralph.mcp.plan_artifact import (
    PlanArtifactValidationError,
    delete_plan_draft,
    finalize_plan_draft,
    is_noop_plan,
    load_plan_draft,
    merge_plan_section,
    new_plan_draft,
    normalize_plan_artifact_content,
    save_plan_draft,
    validate_plan_section,
)

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
