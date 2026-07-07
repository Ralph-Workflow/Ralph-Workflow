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

from ralph.mcp.tools.artifact import (
    handle_submit_plan_section,
    handle_submit_plan_sections,
    handle_validate_plan_draft,
)
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
    assert payload["staged"] is True
    assert payload["section_valid"] is True
    assert payload["can_repair"] is False


def test_submit_plan_section_empty_design_is_staged_with_warning(tmp_path: Path) -> None:
    """Explicitly empty design can be repaired later, but agents should see a warning."""
    workspace = FsWorkspace(tmp_path)

    result = handle_submit_plan_section(
        planning_session(),
        workspace,
        {"section": "design", "mode": "replace", "content": {}},
    )

    assert result.is_error is False, _read_response_text(result)
    payload = json.loads(_read_response_text(result))
    assert payload["submitted"] == ["design"]
    assert payload["staged"] is True
    assert payload["section_valid"] is False
    assert payload["can_repair"] is True
    warnings = cast("list[str]", payload["validation_warnings"])
    assert len(warnings) == 1
    assert "empty design section" in warnings[0]
    draft = _read_draft(tmp_path)
    sections = cast("dict[str, object]", draft["sections"])
    assert sections["design"] == {}


def test_submit_plan_sections_accepts_entries_json_string(tmp_path: Path) -> None:
    """Batched planning submit repairs JSON-string ``entries`` before validation."""
    workspace = FsWorkspace(tmp_path)
    entries = [
        {
            "section": "summary",
            "content": {
                "context": "ctx",
                "scope_items": [
                    {"text": "a", "category": "file_change"},
                    {"text": "b", "category": "test"},
                    {"text": "c", "category": "prompt"},
                ],
            },
        },
        {
            "section": "skills_mcp",
            "content": {"skills": '["writing-plans"]', "mcps": "[]"},
        },
        {
            "section": "steps",
            "content": [
                {
                    "number": 1,
                    "title": "First",
                    "content": "do it",
                    "step_type": "file_change",
                    "targets": '[{"path": "x.py", "action": "modify"}]',
                    "depends_on": "[]",
                    "expected_evidence": '[{"kind": "file", "ref": "x.py"}]',
                }
            ],
        },
    ]

    result = handle_submit_plan_sections(
        planning_session(),
        workspace,
        {"entries": json.dumps(entries)},
    )

    assert result.is_error is False, _read_response_text(result)
    payload = json.loads(_read_response_text(result))
    assert payload["submitted"] == ["summary", "skills_mcp", "steps"]
    assert payload["staged"] is True
    assert payload["section_valid"] is True
    assert payload["can_repair"] is False
    draft = _read_draft(tmp_path)
    sections = cast("dict[str, object]", draft["sections"])
    step = cast("list[dict[str, object]]", sections["steps"])[0]
    assert step["targets"] == [{"path": "x.py", "action": "modify"}]
    assert step.get("depends_on", []) == []
    assert step["expected_evidence"] == [{"kind": "file", "ref": "x.py"}]


def test_submit_plan_sections_repairs_item_wrapped_entries_and_fields(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_sections(
        planning_session(),
        workspace,
        {
            "entries": {
                "item": [
                    {
                        "section": "summary",
                        "content": {
                            "context": "Repair planner wrapper lists.",
                            "scope_items": {
                                "item": [
                                    {"text": "Write a regression test", "category": "test"},
                                    {"text": "Normalize item wrappers", "category": "bugfix"},
                                    {"text": "Run focused MCP tests", "category": "test"},
                                ]
                            },
                        },
                    },
                    {
                        "section": "skills_mcp",
                        "content": {
                            "skills": {"item": "test-driven-development"},
                            "mcps": {"item": []},
                        },
                    },
                ]
            }
        },
    )

    assert result.is_error is False, _read_response_text(result)
    payload = json.loads(_read_response_text(result))
    assert payload["submitted"] == ["summary", "skills_mcp"]
    assert payload["validation_warnings"] == []
    draft = _read_draft(tmp_path)
    sections = cast("dict[str, object]", draft["sections"])
    assert cast("dict[str, object]", sections["skills_mcp"])["skills"] == [
        "test-driven-development"
    ]


def test_submit_plan_sections_repairs_repeated_item_wrapped_entries_and_fields(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_sections(
        planning_session(),
        workspace,
        {
            "entries": {
                "item": {
                    "item": [
                        {
                            "section": "skills_mcp",
                            "content": {
                                "skills": {"item": {"item": "test-driven-development"}},
                                "mcps": {"item": {"item": []}},
                            },
                        }
                    ]
                }
            }
        },
    )

    assert result.is_error is False, _read_response_text(result)
    payload = json.loads(_read_response_text(result))
    assert payload["submitted"] == ["skills_mcp"]
    assert payload["validation_warnings"] == []
    draft = _read_draft(tmp_path)
    sections = cast("dict[str, object]", draft["sections"])
    assert sections["skills_mcp"] == {"skills": ["test-driven-development"]}


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


def test_submit_plan_sections_rejects_empty_skills_even_when_design_is_staged(
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
                    "content": json.dumps({"planning_profile": "strict"}),
                },
            ]
        },
    )

    assert result.is_error is False
    payload = json.loads(_read_response_text(result))
    warnings = cast("list[str]", payload["validation_warnings"])
    assert any("skills_mcp.skills must contain at least one skill name" in w for w in warnings)
    assert (tmp_path / ".agent" / "artifacts" / ".plan_draft.json").exists()

    validate_result = handle_validate_plan_draft(planning_session(), workspace, {})
    assert validate_result.is_error is False
    validate_payload = json.loads(_read_response_text(validate_result))
    assert validate_payload["valid"] is False
    assert "skills_mcp.skills must contain at least one skill name" in _read_response_text(
        validate_result
    )


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


def test_submit_plan_sections_append_steps_stages_invalid_item_with_warning(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_sections(
        planning_session(),
        workspace,
        {"entries": [{"section": "steps", "content": json.dumps("bad"), "mode": "append"}]},
    )

    assert result.is_error is False
    payload = json.loads(_read_response_text(result))
    warnings = cast("list[str]", payload["validation_warnings"])
    assert any("section 'steps' items must be JSON objects" in warning for warning in warnings)

    draft = _read_draft(tmp_path)
    sections = cast("dict[str, object]", draft["sections"])
    assert sections["steps"] == ["bad"]


def test_submit_plan_sections_append_risks_stages_invalid_item_with_warning(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_sections(
        planning_session(),
        workspace,
        {
            "entries": [
                {"section": "risks_mitigations", "content": json.dumps("bad"), "mode": "append"}
            ]
        },
    )

    assert result.is_error is False
    payload = json.loads(_read_response_text(result))
    warnings = cast("list[str]", payload["validation_warnings"])
    assert any(
        "section 'risks_mitigations' items must be JSON objects" in warning
        for warning in warnings
    )

    draft = _read_draft(tmp_path)
    sections = cast("dict[str, object]", draft["sections"])
    assert sections["risks_mitigations"] == ["bad"]


def test_submit_plan_sections_rejects_single_step_object_for_replace(tmp_path: Path) -> None:
    """A single step object (not a list) is rejected with an
    ``isError=True`` tool result for the ``steps`` section in
    replace mode because the shape check requires a JSON array,
    not a single object.
    """
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_sections(
        planning_session(),
        workspace,
        {
            "entries": [
                {
                    "section": "steps",
                    "content": json.dumps(
                        {
                            "number": 1,
                            "title": "One",
                            "content": "single object instead of list",
                        }
                    ),
                }
            ]
        },
    )

    assert result.is_error is True
    payload = json.loads(_read_response_text(result))
    assert payload.get("submitted") == []
    assert payload.get("failed_at") == 0
    assert "section 'steps' with mode='replace' must be a JSON array" in payload.get(
        "error", ""
    )


def test_submit_plan_sections_missing_entries_raises(tmp_path: Path) -> None:
    """Missing 'entries' array raises InvalidParamsError."""
    workspace = FsWorkspace(tmp_path)
    with pytest.raises(InvalidParamsError) as exc_info:
        handle_submit_plan_sections(planning_session(), workspace, {})

    message = str(exc_info.value)
    assert "Missing 'entries' array" in message
    assert ".agent/artifact-formats/plan.md" in message
    assert '{"entries":[{"section":"summary"' in message
    assert "{'" not in message


def test_submit_plan_sections_unknown_section_includes_fix_guidance(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_sections(
        planning_session(),
        workspace,
        {"entries": [{"section": "bogus", "content": json.dumps({})}]},
    )

    assert result.is_error is True
    payload = json.loads(_read_response_text(result))
    error = cast("str", payload["error"])
    assert ".agent/artifact-formats/plan.md" in error
    assert "Unknown plan section 'bogus'" in error
    assert '{"entries":[{"section":"summary"' in error
    assert "{'" not in error
