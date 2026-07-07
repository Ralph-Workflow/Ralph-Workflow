"""Tests for the ralph_patch_step MCP tool (partial step update).

The tests cover:

- patch step 3 with {verify_command: 'new cmd'} preserves all other fields.
- patch step 3 with {depends_on: [1, 2]} overwrites the depends_on and triggers the
  reindex/AC remap as for replace.
- patch step 3 with {title: 'new title'} returns the same echo payload shape as replace.
- patch with step.number in the payload ignores the number (same as replace).
- patch on a non-existent step_number raises InvalidParamsError.

The tests use only in-memory Pydantic + the existing tool handlers
(no real I/O, no real subprocess, no time.sleep). All tests are fully
type-annotated.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest

from ralph.mcp.tools.coordination import InvalidParamsError
from ralph.mcp.tools.plan_draft_edit import handle_patch_step, handle_replace_plan_step
from ralph.workspace.fs import FsWorkspace
from tests.test_artifact_format_docs_mock_session import planning_session

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.tool_content import ToolContent


def _write_draft(tmp_path: Path, draft: dict[str, object]) -> None:
    artifact_dir = tmp_path / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / ".plan_draft.json").write_text(json.dumps(draft), encoding="utf-8")


def _read_draft(tmp_path: Path) -> dict[str, object]:
    artifact_dir = tmp_path / ".agent" / "artifacts"
    return cast(
        "dict[str, object]",
        json.loads((artifact_dir / ".plan_draft.json").read_text(encoding="utf-8")),
    )


def _read_response_text(result: object) -> str:
    content = cast("list[ToolContent]", result.content)
    return cast("str", content[0].text)


def _three_step_draft() -> dict[str, object]:
    return {
        "schema_version": 1,
        "started_at": "2026-05-20T00:00:00+00:00",
        "updated_at": "2026-05-20T00:00:00+00:00",
        "sections": {
            "summary": {
                "context": "ctx",
                "scope_items": [
                    {"text": "a", "category": "file_change"},
                    {"text": "b", "category": "test"},
                    {"text": "c", "category": "prompt"},
                ],
            },
            "skills_mcp": {"skills": ["writing-plans"], "mcps": []},
            "steps": [
                {
                    "number": 1,
                    "title": "A",
                    "content": "a",
                    "step_type": "action",
                    "depends_on": [],
                },
                {
                    "number": 2,
                    "title": "B",
                    "content": "b",
                    "step_type": "verify",
                    "verify_command": "pytest tests/test_b.py -q",
                    "depends_on": [1],
                },
                {
                    "number": 3,
                    "title": "C",
                    "content": "c",
                    "step_type": "action",
                    "depends_on": [2],
                },
            ],
            "critical_files": {
                "primary_files": [{"path": "a.py", "action": "modify"}],
            },
            "risks_mitigations": [
                {"risk": "r", "mitigation": "m", "severity": "low"},
            ],
            "verification_strategy": [{"method": "pytest", "expected_outcome": "ok"}],
        },
    }


def test_patch_step_preserves_unmentioned_fields(tmp_path: Path) -> None:
    """patch step 3 with {verify_command: 'new cmd'} preserves all other fields."""
    _write_draft(tmp_path, _three_step_draft())
    workspace = FsWorkspace(tmp_path)
    result = handle_patch_step(
        planning_session(),
        workspace,
        {
            "step_number": 3,
            "step": {"verify_command": "pytest tests/test_new.py -q"},
        },
    )
    assert result.is_error is False
    payload = json.loads(_read_response_text(result))
    assert payload["action"] == "replace"
    assert "step_number" in payload

    # Verify field preservation
    draft = _read_draft(tmp_path)
    steps = cast("list[dict[str, object]]", cast("dict[str, object]", draft["sections"])["steps"])
    c_step = next(s for s in steps if cast("int", s["number"]) == 3)
    assert c_step["verify_command"] == "pytest tests/test_new.py -q"
    # Unmentioned fields are preserved
    assert c_step["title"] == "C"
    assert c_step["content"] == "c"
    assert c_step["depends_on"] == [2]


def test_patch_step_repairs_existing_string_step_number(tmp_path: Path) -> None:
    """patch step accepts a plausible staged draft whose number field is a string."""
    draft = _three_step_draft()
    sections = cast("dict[str, object]", draft["sections"])
    steps = cast("list[dict[str, object]]", sections["steps"])
    steps[2]["number"] = "3"
    _write_draft(tmp_path, draft)
    workspace = FsWorkspace(tmp_path)

    result = handle_patch_step(
        planning_session(),
        workspace,
        {
            "step_number": 3,
            "step": {"content": "patched after string-number repair"},
        },
    )

    assert result.is_error is False
    payload = json.loads(_read_response_text(result))
    assert payload["validation_warnings"] == []
    updated = _read_draft(tmp_path)
    updated_sections = cast("dict[str, object]", updated["sections"])
    updated_steps = cast("list[dict[str, object]]", updated_sections["steps"])
    patched = next(step for step in updated_steps if step["number"] == 3)
    assert patched["content"] == "patched after string-number repair"
    assert [step["number"] for step in updated_steps] == [1, 2, 3]


def test_patch_step_overwrite_depends_on_triggers_remap(tmp_path: Path) -> None:
    """patch with {depends_on: [1, 2]} overwrites the depends_on
    and triggers the reindex/AC remap.
    """
    _write_draft(tmp_path, _three_step_draft())
    workspace = FsWorkspace(tmp_path)
    result = handle_patch_step(
        planning_session(),
        workspace,
        {"step_number": 3, "step": {"depends_on": [1, 2]}},
    )
    assert result.is_error is False
    payload = json.loads(_read_response_text(result))
    assert payload["action"] == "replace"
    assert "reindex_map" in payload
    assert "rewritten_depends_on" in payload


def test_patch_step_title_returns_echo_shape(tmp_path: Path) -> None:
    """patch with {title: 'new title'} returns the same echo payload shape as replace."""
    _write_draft(tmp_path, _three_step_draft())
    workspace = FsWorkspace(tmp_path)
    result = handle_patch_step(
        planning_session(),
        workspace,
        {"step_number": 3, "step": {"title": "Renamed"}},
    )
    assert result.is_error is False
    payload = json.loads(_read_response_text(result))
    for required_key in (
        "action",
        "reindex_map",
        "rewritten_depends_on",
        "rewritten_ac_satisfied_by_steps",
        "dropped_ac_satisfied_by_steps",
        "total_steps",
    ):
        assert required_key in payload, f"Echo payload missing key {required_key!r}"
    # The title is updated
    draft = _read_draft(tmp_path)
    steps = cast("list[dict[str, object]]", cast("dict[str, object]", draft["sections"])["steps"])
    c_step = next(s for s in steps if cast("int", s["number"]) == 3)
    assert c_step["title"] == "Renamed"


def test_patch_step_ignores_step_number_in_payload(tmp_path: Path) -> None:
    """patch with step.number in the payload ignores the number (same as replace)."""
    _write_draft(tmp_path, _three_step_draft())
    workspace = FsWorkspace(tmp_path)
    result = handle_patch_step(
        planning_session(),
        workspace,
        {
            "step_number": 2,
            "step": {"title": "Renamed B", "number": 99},
        },
    )
    assert result.is_error is False
    # Step 2's title changed, but the step is still numbered 2 (not 99)
    draft = _read_draft(tmp_path)
    steps = cast("list[dict[str, object]]", cast("dict[str, object]", draft["sections"])["steps"])
    b_step = next(s for s in steps if cast("int", s["number"]) == 2)
    assert b_step["title"] == "Renamed B"
    # No step 99 exists
    assert all(cast("int", s["number"]) != 99 for s in steps)


def test_patch_step_nonexistent_step_raises(tmp_path: Path) -> None:
    """patch on a non-existent step_number raises InvalidParamsError."""
    _write_draft(tmp_path, _three_step_draft())
    workspace = FsWorkspace(tmp_path)
    with pytest.raises(InvalidParamsError, match="does not exist"):
        handle_patch_step(
            planning_session(),
            workspace,
            {"step_number": 99, "step": {"title": "nope"}},
        )


def test_patch_step_missing_step_object_raises(tmp_path: Path) -> None:
    """patch with no 'step' object raises InvalidParamsError."""
    _write_draft(tmp_path, _three_step_draft())
    workspace = FsWorkspace(tmp_path)
    with pytest.raises(InvalidParamsError, match="Missing 'step' object"):
        handle_patch_step(
            planning_session(),
            workspace,
            {"step_number": 1, "step": "not a dict"},
        )


def test_replace_plan_step_partial_payload_points_to_patch_step(tmp_path: Path) -> None:
    """Full replacement with partial fields should tell agents to use patch_step."""
    _write_draft(tmp_path, _three_step_draft())
    workspace = FsWorkspace(tmp_path)

    result = handle_replace_plan_step(
        planning_session(),
        workspace,
        {"step_number": 3, "step": {"satisfies": []}},
    )

    assert result.is_error is False
    payload = json.loads(_read_response_text(result))
    warnings = cast("list[str]", payload["validation_warnings"])
    assert any("ralph_patch_step" in warning for warning in warnings)
    assert any("partial update" in warning for warning in warnings)
