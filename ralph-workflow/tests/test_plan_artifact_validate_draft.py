"""Tests for the ralph_validate_draft MCP tool (read-only cross-section validator).

The tests cover:

- Empty draft returns valid=True.
- Draft with valid steps + design returns valid=True.
- Draft with a depends_on cycle returns valid=False with the named entry step.
- Draft with an AC referencing a non-existent step returns valid=False.
- Draft with parallel_plan AND work_units both populated returns valid=False.
- validate_draft does NOT modify the on-disk draft (uses tmp_path).

The tests use only in-memory Pydantic + the existing tool handlers
(no real I/O, no real subprocess, no time.sleep). All tests are fully
type-annotated (no mypy suppressions per
ralph-workflow/ralph/testing/audit_typecheck_bypass.py).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ralph.mcp.tools.artifact import handle_validate_plan_draft
from ralph.workspace.fs import FsWorkspace
from tests.test_artifact_format_docs_mock_session import planning_session

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.tool_content import ToolContent


def _write_draft(tmp_path: Path, draft: dict[str, object]) -> Path:
    artifact_dir = tmp_path / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / ".plan_draft.json").write_text(json.dumps(draft), encoding="utf-8")
    return artifact_dir


def _minimal_valid_draft() -> dict[str, object]:
    return {
        "schema_version": 1,
        "started_at": "2026-05-20T00:00:00+00:00",
        "updated_at": "2026-05-20T00:00:00+00:00",
        "sections": {
            "summary": {
                "context": "Context",
                "scope_items": [
                    {"text": "One", "category": "file_change"},
                    {"text": "Two", "category": "test"},
                    {"text": "Three", "category": "prompt"},
                ],
            },
            "skills_mcp": {
                "skills": ["writing-plans"],
                "mcps": [],
            },
            "steps": [
                {"number": 1, "title": "First", "content": "first", "depends_on": []},
            ],
            "critical_files": {
                "primary_files": [{"path": "a.py", "action": "modify"}],
                "reference_files": [],
            },
            "risks_mitigations": [
                {"severity": "medium", "risk": "Risk", "mitigation": "Mitigation"}
            ],
            "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
        },
    }


def _read_response_text(result: object) -> str:
    content = cast("list[ToolContent]", result.content)
    return cast("str", content[0].text)


def test_validate_draft_no_draft_returns_valid_empty(tmp_path: Path) -> None:
    """When no draft exists, validate returns valid=True with empty staged sections."""
    workspace = FsWorkspace(tmp_path)
    result = handle_validate_plan_draft(planning_session(), workspace, {})
    payload = json.loads(_read_response_text(result))
    assert payload == {
        "valid": True,
        "errors": [],
        "staged_sections": [],
    }
    assert result.is_error is False


def test_validate_draft_valid_minimal_draft_returns_valid_true(tmp_path: Path) -> None:
    """A minimal but valid draft returns valid=True with the staged sections listed."""
    _write_draft(tmp_path, _minimal_valid_draft())
    workspace = FsWorkspace(tmp_path)
    result = handle_validate_plan_draft(planning_session(), workspace, {})
    payload = json.loads(_read_response_text(result))
    assert payload["valid"] is True
    assert payload["errors"] == []
    staged = cast("list[str]", payload["staged_sections"])
    assert "summary" in staged
    assert "steps" in staged
    assert "design" not in staged
    assert result.is_error is False


def test_validate_draft_valid_draft_with_design_returns_valid_true(tmp_path: Path) -> None:
    """A draft with a valid design sub-section also returns valid=True."""
    draft = _minimal_valid_draft()
    design_section = cast("dict[str, object]", draft["sections"])
    design_section["design"] = {
        "testability": {"must_be_black_box": True},
        "acceptance_criteria": {
            "criteria": [{"id": "AC-01", "description": "All good", "satisfied_by_steps": [1]}]
        },
    }
    _write_draft(tmp_path, draft)
    workspace = FsWorkspace(tmp_path)
    result = handle_validate_plan_draft(planning_session(), workspace, {})
    payload = json.loads(_read_response_text(result))
    assert payload["valid"] is True


def test_validate_draft_depends_on_cycle_returns_invalid(tmp_path: Path) -> None:
    """A draft with a depends_on cycle is rejected with the cycle entry named in errors."""
    draft = _minimal_valid_draft()
    steps = cast("list[dict[str, object]]", draft["sections"]["steps"])
    steps.clear()
    steps.extend(
        [
            {"number": 1, "title": "A", "content": "a", "depends_on": [3]},
            {"number": 2, "title": "B", "content": "b", "depends_on": [1]},
            {"number": 3, "title": "C", "content": "c", "depends_on": [2]},
        ]
    )
    _write_draft(tmp_path, draft)
    workspace = FsWorkspace(tmp_path)
    result = handle_validate_plan_draft(planning_session(), workspace, {})
    payload = json.loads(_read_response_text(result))
    assert payload["valid"] is False
    errors = cast("list[dict[str, str]]", payload["errors"])
    assert len(errors) >= 1
    assert "cycle" in errors[0]["message"].lower()


def test_validate_draft_ac_orphan_step_returns_invalid(tmp_path: Path) -> None:
    """A draft whose AC.satisfied_by_steps references a non-existent step is invalid."""
    draft = _minimal_valid_draft()
    design = {
        "testability": {"must_be_black_box": True},
        "acceptance_criteria": {
            "criteria": [{"id": "AC-01", "description": "ok", "satisfied_by_steps": [99]}]
        },
    }
    cast("dict[str, object]", draft["sections"])["design"] = design
    _write_draft(tmp_path, draft)
    workspace = FsWorkspace(tmp_path)
    result = handle_validate_plan_draft(planning_session(), workspace, {})
    payload = json.loads(_read_response_text(result))
    assert payload["valid"] is False
    errors = cast("list[dict[str, str]]", payload["errors"])
    assert any("99" in e["message"] or "step" in e["message"].lower() for e in errors)


def test_validate_draft_parallel_plan_and_work_units_returns_invalid(tmp_path: Path) -> None:
    """A draft that declares both parallel_plan AND work_units is invalid."""
    draft = _minimal_valid_draft()
    sections = cast("dict[str, object]", draft["sections"])
    sections["parallel_plan"] = [
        {
            "id": "pu-1",
            "description": "parallel chunk",
            "edit_area": {"paths": ["a.py"], "directories": []},
            "depends_on": [],
        }
    ]
    sections["work_units"] = [
        {
            "unit_id": "wu-1",
            "description": "work unit",
            "allowed_directories": ["a.py"],
        }
    ]
    _write_draft(tmp_path, draft)
    workspace = FsWorkspace(tmp_path)
    result = handle_validate_plan_draft(planning_session(), workspace, {})
    payload = json.loads(_read_response_text(result))
    assert payload["valid"] is False
    errors = cast("list[dict[str, str]]", payload["errors"])
    assert any("parallel_plan" in e["message"] and "work_units" in e["message"] for e in errors)


def test_validate_draft_does_not_modify_on_disk_draft(tmp_path: Path) -> None:
    """validate_draft is read-only: the on-disk draft file is unchanged after the call."""
    draft = _minimal_valid_draft()
    artifact_dir = _write_draft(tmp_path, draft)
    workspace = FsWorkspace(tmp_path)
    handle_validate_plan_draft(planning_session(), workspace, {})
    raw_after = (artifact_dir / ".plan_draft.json").read_text(encoding="utf-8")
    assert json.loads(raw_after) == draft


def test_validate_draft_does_not_delete_draft_file(tmp_path: Path) -> None:
    """validate_draft does NOT delete the on-disk draft (unlike finalize_plan)."""
    artifact_dir = _write_draft(tmp_path, _minimal_valid_draft())
    workspace = FsWorkspace(tmp_path)
    handle_validate_plan_draft(planning_session(), workspace, {})
    assert (artifact_dir / ".plan_draft.json").exists()
