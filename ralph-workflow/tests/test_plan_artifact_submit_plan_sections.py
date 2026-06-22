"""Tests for the ralph_submit_plan_sections MCP tool (batched section submit).

The tests cover:

- Batch of valid [summary, skills_mcp, steps] entries returns submitted=[...] and the
  draft has all 3 sections.
- Batch with one invalid entry (e.g. unknown section name) returns
  failed_at=<index> and the draft is UNCHANGED.
- mode='append' on a list section works.
- mode='append' on an object section returns InvalidParamsError.
- Empty batch returns submitted=[] (no error).

The tests use only in-memory Pydantic + the existing tool handlers
(no real I/O, no real subprocess, no time.sleep). All tests are fully
type-annotated.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest

from ralph.mcp.tools.artifact import handle_submit_plan_sections
from ralph.mcp.tools.coordination import InvalidParamsError
from ralph.workspace.fs import FsWorkspace
from tests.test_artifact_format_docs_mock_session import planning_session

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.tool_content import ToolContent


def _read_draft(tmp_path: Path) -> dict[str, object]:
    artifact_dir = tmp_path / ".agent" / "artifacts"
    return cast(
        "dict[str, object]",
        json.loads((artifact_dir / ".plan_draft.json").read_text(encoding="utf-8")),
    )


def _read_response_text(result: object) -> str:
    content = cast("list[ToolContent]", result.content)
    return cast("str", content[0].text)


def test_submit_plan_sections_empty_batch_returns_empty_submitted(tmp_path: Path) -> None:
    """An empty batch returns submitted=[] and a successful response."""
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_sections(planning_session(), workspace, {"entries": []})
    assert result.is_error is False
    payload = json.loads(_read_response_text(result))
    assert payload["submitted"] == []
    assert payload["staged_sections"] == []


def test_submit_plan_sections_all_valid_sections_are_staged(tmp_path: Path) -> None:
    """A batch of [summary, skills_mcp, steps] all valid returns submitted=[...] and the draft
    has all 3 sections."""
    workspace = FsWorkspace(tmp_path)
    entries = [
        {
            "section": "summary",
            "content": json.dumps(
                {
                    "context": "ctx",
                    "scope_items": [
                        {"text": "a", "category": "file_change"},
                        {"text": "b", "category": "test"},
                        {"text": "c", "category": "prompt"},
                    ],
                }
            ),
        },
        {
            "section": "skills_mcp",
            "content": json.dumps({"skills": ["writing-plans"], "mcps": []}),
        },
        {
            "section": "steps",
            "content": json.dumps(
                [
                    {
                        "number": 1,
                        "title": "First",
                        "content": "do it",
                        "step_type": "verify",
                        "verify_command": "pytest tests/test_x.py -q",
                    }
                ]
            ),
        },
    ]
    result = handle_submit_plan_sections(planning_session(), workspace, {"entries": entries})
    payload = json.loads(_read_response_text(result))
    assert payload["submitted"] == ["summary", "skills_mcp", "steps"]
    staged = cast("list[str]", payload["staged_sections"])
    assert "summary" in staged
    assert "skills_mcp" in staged
    assert "steps" in staged
    assert result.is_error is False

    # Verify the draft was actually saved with all 3 sections
    draft = _read_draft(tmp_path)
    sections = cast("dict[str, object]", draft["sections"])
    assert "summary" in sections
    assert "skills_mcp" in sections
    assert "steps" in sections


def test_submit_plan_sections_one_invalid_section_rejects_entire_batch(tmp_path: Path) -> None:
    """A batch with an unknown section name returns failed_at=<index> and the draft is UNCHANGED.

    The all-or-nothing semantics is the contract: no partial commit when one entry fails.
    """
    workspace = FsWorkspace(tmp_path)
    entries = [
        {
            "section": "summary",
            "content": json.dumps(
                {
                    "context": "ctx",
                    "scope_items": [
                        {"text": "a"},
                        {"text": "b"},
                        {"text": "c"},
                    ],
                }
            ),
        },
        {
            "section": "nonexistent_section",
            "content": json.dumps({"foo": "bar"}),
        },
    ]
    result = handle_submit_plan_sections(planning_session(), workspace, {"entries": entries})
    assert result.is_error is True
    payload = json.loads(_read_response_text(result))
    assert payload["submitted"] == []
    assert payload["failed_at"] == 1
    assert "Unknown plan section" in payload["error"] or "nonexistent_section" in payload["error"]
    # Draft is unchanged (no on-disk draft should exist)
    artifact_dir = tmp_path / ".agent" / "artifacts"
    assert not (artifact_dir / ".plan_draft.json").exists()


def test_submit_plan_sections_mode_append_on_list_section_works(tmp_path: Path) -> None:
    """mode='append' on a list section merges the new entries into the existing list."""
    workspace = FsWorkspace(tmp_path)
    first = handle_submit_plan_sections(
        planning_session(),
        workspace,
        {
            "entries": [
                {
                    "section": "steps",
                    "content": json.dumps(
                        [
                            {
                                "number": 1,
                                "title": "First",
                                "content": "first",
                                "step_type": "verify",
                                "verify_command": "pytest tests/test_x.py -q",
                            }
                        ]
                    ),
                }
            ]
        },
    )
    assert first.is_error is False
    second = handle_submit_plan_sections(
        planning_session(),
        workspace,
        {
            "entries": [
                {
                    "section": "steps",
                    "content": json.dumps(
                        [
                            {
                                "number": 2,
                                "title": "Second",
                                "content": "second",
                                "step_type": "verify",
                                "verify_command": "pytest tests/test_y.py -q",
                            }
                        ]
                    ),
                    "mode": "append",
                }
            ]
        },
    )
    assert second.is_error is False
    payload = json.loads(_read_response_text(second))
    assert payload["submitted"] == ["steps"]
    draft = _read_draft(tmp_path)
    steps = cast("list[dict[str, object]]", cast("dict[str, object]", draft["sections"])["steps"])
    titles = [cast("str", s["title"]) for s in steps]
    assert "First" in titles
    assert "Second" in titles


def test_submit_plan_sections_mode_append_accepts_single_item_payload(tmp_path: Path) -> None:
    """Batch append accepts the same single-item payload shape as single-section append."""
    workspace = FsWorkspace(tmp_path)
    first = handle_submit_plan_sections(
        planning_session(),
        workspace,
        {
            "entries": [
                {
                    "section": "steps",
                    "content": json.dumps(
                        [
                            {
                                "number": 1,
                                "title": "First",
                                "content": "first",
                                "step_type": "verify",
                                "verify_command": "pytest tests/test_x.py -q",
                            }
                        ]
                    ),
                }
            ]
        },
    )
    assert first.is_error is False

    second = handle_submit_plan_sections(
        planning_session(),
        workspace,
        {
            "entries": [
                {
                    "section": "steps",
                    "content": json.dumps(
                        {
                            "number": 2,
                            "title": "Second",
                            "content": "second",
                            "step_type": "verify",
                            "verify_command": "pytest tests/test_y.py -q",
                        }
                    ),
                    "mode": "append",
                }
            ]
        },
    )

    assert second.is_error is False
    draft = _read_draft(tmp_path)
    steps = cast("list[dict[str, object]]", cast("dict[str, object]", draft["sections"])["steps"])
    assert [cast("str", step["title"]) for step in steps] == ["First", "Second"]


def test_submit_plan_sections_allows_empty_skills_when_minimal_design_is_staged(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_sections(
        planning_session(),
        workspace,
        {
            "entries": [
                {
                    "section": "summary",
                    "content": json.dumps(
                        {
                            "context": "ctx",
                            "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
                        }
                    ),
                },
                {
                    "section": "skills_mcp",
                    "content": json.dumps({"skills": [], "mcps": []}),
                },
                {
                    "section": "steps",
                    "content": json.dumps(
                        [
                            {
                                "number": 1,
                                "title": "First",
                                "content": "do it",
                                "step_type": "verify",
                                "verify_command": "pytest tests/test_x.py -q",
                            }
                        ]
                    ),
                },
                {
                    "section": "critical_files",
                    "content": json.dumps(
                        {"primary_files": [{"path": "a.py", "action": "modify"}]}
                    ),
                },
                {
                    "section": "risks_mitigations",
                    "content": json.dumps([{"risk": "r", "mitigation": "m"}]),
                },
                {
                    "section": "verification_strategy",
                    "content": json.dumps([{"method": "pytest", "expected_outcome": "passes"}]),
                },
                {
                    "section": "design",
                    "content": json.dumps({"planning_profile": "minimal"}),
                },
            ]
        },
    )

    assert result.is_error is False
    draft = _read_draft(tmp_path)
    sections = cast("dict[str, object]", draft["sections"])
    skills_mcp = cast("dict[str, object]", sections["skills_mcp"])
    skills = cast("list[str]", skills_mcp["skills"])
    assert skills == []


def test_submit_plan_sections_mode_append_on_object_section_rejected(tmp_path: Path) -> None:
    """mode='append' on an object section is rejected (object sections accept only replace)."""
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_sections(
        planning_session(),
        workspace,
        {
            "entries": [
                {
                    "section": "summary",
                    "content": json.dumps(
                        {
                            "context": "ctx",
                            "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
                        }
                    ),
                    "mode": "append",
                }
            ]
        },
    )
    assert result.is_error is True
    payload = json.loads(_read_response_text(result))
    err = payload.get("error", "").lower()
    assert "summary" in err or "replace" in err


def test_submit_plan_sections_missing_entries_raises(tmp_path: Path) -> None:
    """Missing 'entries' array raises InvalidParamsError."""
    workspace = FsWorkspace(tmp_path)
    with pytest.raises(InvalidParamsError, match="Missing 'entries' array"):
        handle_submit_plan_sections(planning_session(), workspace, {})
