"""Tests for the read-after-write echo payload of the 4 step-mutation MCP tools.

Each test asserts on the new JSON dict shape (not the old literal string).
The echo payload is documented in
``.agent/artifact-formats/plan.md`` §'Step-mutation read-after-write echo'.

Tests:
- insert returns {action, new_step_number, reindex_map, rewritten_depends_on,
  rewritten_ac_satisfied_by_steps, dropped_ac_satisfied_by_steps, total_steps}.
- replace returns the same shape with step_number preserved (no new_step_number).
- remove returns the same shape with removed_step_number and the new total_steps.
- move returns the same shape with from_step_number and to_index.

The tests use only in-memory Pydantic + the existing tool handlers
(no real I/O, no real subprocess, no time.sleep). All tests are fully
type-annotated.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ralph.mcp.tools.plan_draft_edit import (
    handle_insert_plan_step,
    handle_move_plan_step,
    handle_remove_plan_step,
    handle_replace_plan_step,
)
from ralph.workspace.fs import FsWorkspace
from tests.test_artifact_format_docs_mock_session import planning_session

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.tool_content import ToolContent


def _write_draft(tmp_path: Path, draft: dict[str, object]) -> None:
    artifact_dir = tmp_path / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / ".plan_draft.json").write_text(
        json.dumps(draft), encoding="utf-8"
    )


def _read_response_text(result: object) -> str:
    content = cast("list[ToolContent]", result.content)
    return cast("str", content[0].text)


def _read_response_json(result: object) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(_read_response_text(result)))


def _three_step_draft_with_ac() -> dict[str, object]:
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
            "design": {
                "acceptance_criteria": {
                    "criteria": [
                        {
                            "id": "AC-01",
                            "description": "ok",
                            "satisfied_by_steps": [1, 2, 3],
                        }
                    ]
                }
            },
        },
    }


def test_insert_returns_echo_payload_shape(tmp_path: Path) -> None:
    """insert returns the new_step_number, reindex_map, rewritten lists, and total_steps."""
    _write_draft(tmp_path, _three_step_draft_with_ac())
    workspace = FsWorkspace(tmp_path)
    result = handle_insert_plan_step(
        planning_session(),
        workspace,
        {
            "index": 2,
            "step": {
                "number": 99,
                "title": "Inserted",
                "content": "inserted",
                "step_type": "action",
                "depends_on": [1],
            },
        },
    )
    assert result.is_error is False
    payload = _read_response_json(result)
    # Required keys
    for key in (
        "action",
        "new_step_number",
        "reindex_map",
        "rewritten_depends_on",
        "rewritten_ac_satisfied_by_steps",
        "dropped_ac_satisfied_by_steps",
        "total_steps",
    ):
        assert key in payload, f"insert echo missing key {key!r}"
    assert payload["action"] == "insert"
    # The inserted step is assigned a new number
    assert isinstance(payload["new_step_number"], int)
    assert cast("int", payload["new_step_number"]) > 3
    # Reindex map has 4 entries
    reindex_map = cast("dict[str, int], object", payload["reindex_map"])
    assert len(reindex_map) == 4
    # Total steps is 4 after insert
    assert payload["total_steps"] == 4
    # The AC's satisfied_by_steps was rewritten (the old 1, 2, 3 -> new 1, 3, 4)
    rewritten = cast("list[str], object", payload["rewritten_ac_satisfied_by_steps"])
    assert "AC-01" in rewritten


def test_replace_returns_echo_payload_shape(tmp_path: Path) -> None:
    """replace returns the same shape with step_number preserved (no new_step_number)."""
    _write_draft(tmp_path, _three_step_draft_with_ac())
    workspace = FsWorkspace(tmp_path)
    result = handle_replace_plan_step(
        planning_session(),
        workspace,
        {
            "step_number": 2,
            "step": {
                "title": "Renamed B",
                "content": "renamed",
                "step_type": "action",
                "depends_on": [1],
            },
        },
    )
    assert result.is_error is False
    payload = _read_response_json(result)
    for key in (
        "action",
        "step_number",
        "reindex_map",
        "rewritten_depends_on",
        "rewritten_ac_satisfied_by_steps",
        "dropped_ac_satisfied_by_steps",
        "total_steps",
    ):
        assert key in payload, f"replace echo missing key {key!r}"
    assert payload["action"] == "replace"
    assert payload["step_number"] == 2
    # No new_step_number in replace echo
    assert "new_step_number" not in payload
    # Reindex map has 3 entries (number preserved)
    reindex_map = cast("dict[str, int], object", payload["reindex_map"])
    assert len(reindex_map) == 3
    assert payload["total_steps"] == 3


def test_remove_returns_echo_payload_shape(tmp_path: Path) -> None:
    """remove returns the same shape with removed_step_number and the new total_steps."""
    _write_draft(tmp_path, _three_step_draft_with_ac())
    workspace = FsWorkspace(tmp_path)
    # Remove step 3; nothing depends on it (steps 1 and 2 only depend on each other).
    result = handle_remove_plan_step(
        planning_session(),
        workspace,
        {"step_number": 3},
    )
    assert result.is_error is False
    payload = _read_response_json(result)
    for key in (
        "action",
        "removed_step_number",
        "reindex_map",
        "rewritten_depends_on",
        "rewritten_ac_satisfied_by_steps",
        "dropped_ac_satisfied_by_steps",
        "total_steps",
    ):
        assert key in payload, f"remove echo missing key {key!r}"
    assert payload["action"] == "remove"
    assert payload["removed_step_number"] == 3
    # total_steps is 2 after removal
    assert payload["total_steps"] == 2
    # AC-01 lost the step-3 reference so it's in dropped_ac_satisfied_by_steps
    dropped = cast("list[str], object", payload["dropped_ac_satisfied_by_steps"])
    assert "AC-01" in dropped


def test_move_returns_echo_payload_shape(tmp_path: Path) -> None:
    """move returns the same shape with from_step_number and to_index."""
    _write_draft(tmp_path, _three_step_draft_with_ac())
    workspace = FsWorkspace(tmp_path)
    result = handle_move_plan_step(
        planning_session(),
        workspace,
        {"from_step_number": 3, "to_index": 1},
    )
    assert result.is_error is False
    payload = _read_response_json(result)
    for key in (
        "action",
        "from_step_number",
        "to_index",
        "reindex_map",
        "rewritten_depends_on",
        "rewritten_ac_satisfied_by_steps",
        "dropped_ac_satisfied_by_steps",
        "total_steps",
    ):
        assert key in payload, f"move echo missing key {key!r}"
    assert payload["action"] == "move"
    assert payload["from_step_number"] == 3
    assert payload["to_index"] == 1
    # Reindex map has 3 entries (step numbers preserved by move)
    reindex_map = cast("dict[str, int], object", payload["reindex_map"])
    assert len(reindex_map) == 3
