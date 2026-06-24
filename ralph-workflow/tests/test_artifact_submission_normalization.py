"""Boundary normalization tests for artifact and plan-section submissions."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest

from ralph.mcp.tools.artifact import (
    handle_submit_plan_section,
    handle_submit_plan_sections,
    handle_validate_plan_draft,
    prepare_artifact_submission,
)
from ralph.mcp.tools.coordination import InvalidParamsError, ToolResult
from ralph.mcp.tools.tool_content import ToolContent
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path


class _PlanningSession:
    session_id = "planning-test-session"
    run_id = "run-test"
    drain = "planning"

    def check_capability(self, capability: str) -> object:
        return capability in {"artifact.submit", "artifact.plan_read", "artifact.plan_write"}


def _planning_session() -> _PlanningSession:
    return _PlanningSession()


def _json_text(value: object) -> str:
    return json.dumps(value)


def _analysis_request_changes_payload() -> dict[str, object]:
    return {
        "status": "request_changes",
        "summary": "Plan needs stronger validation coverage.",
        "what_came_up_short": ["missing malformed JSON case"],
        "how_to_fix": ["Add a failing malformed JSON test before implementation"],
    }


def _valid_plan_payload() -> dict[str, object]:
    return {
        "summary": {
            "context": "Harden artifact submission parsing.",
            "scope_items": [{"text": "normalize"}, {"text": "validate"}, {"text": "verify"}],
        },
        "skills_mcp": {"skills": ["test-driven-development"], "mcps": []},
        "steps": [
            {
                "number": 1,
                "title": "Add parser tests",
                "content": "Add focused parser tests.",
                "step_type": "verify",
                "verify_command": "pytest tests/test_artifact_submission_normalization.py -q",
            }
        ],
        "critical_files": {
            "primary_files": [{"path": "ralph/mcp/tools/artifact.py", "action": "modify"}]
        },
        "risks_mitigations": [
            {"risk": "Ambiguous parsing", "mitigation": "Only accept one identifiable container"}
        ],
        "verification_strategy": [
            {
                "method": "pytest tests/test_artifact_submission_normalization.py -q",
                "expected_outcome": "pass",
            }
        ],
    }


def _read_response_text(result: object) -> str:
    tool_result = cast("ToolResult", result)
    first = tool_result.content[0]
    assert isinstance(first, ToolContent)
    return first.text


def _read_draft_sections(tmp_path: Path) -> dict[str, object]:
    raw = (tmp_path / ".agent" / "artifacts" / ".plan_draft.json").read_text(encoding="utf-8")
    draft = cast("dict[str, object]", json.loads(raw))
    return cast("dict[str, object]", draft["sections"])


def test_artifact_submit_accepts_native_dict_for_content_field(tmp_path: Path) -> None:
    artifact_type, normalized = prepare_artifact_submission(
        {
            "artifact_type": "planning_analysis_decision",
            "content": _analysis_request_changes_payload(),
        },
        session_drain="planning_analysis",
        base_path=tmp_path,
    )

    assert artifact_type == "planning_analysis_decision"
    assert normalized["what_came_up_short"] == ["missing malformed JSON case"]


def test_artifact_submit_unwraps_double_encoded_content_json(tmp_path: Path) -> None:
    content = _json_text(_json_text(_analysis_request_changes_payload()))

    artifact_type, normalized = prepare_artifact_submission(
        {
            "artifact_type": "planning_analysis_decision",
            "content": content,
        },
        session_drain="planning_analysis",
        base_path=tmp_path,
    )

    assert artifact_type == "planning_analysis_decision"
    assert normalized["how_to_fix"] == ["Add a failing malformed JSON test before implementation"]


def test_artifact_submit_decodes_json_string_for_list_fields(tmp_path: Path) -> None:
    payload = _analysis_request_changes_payload()
    payload["what_came_up_short"] = _json_text(["missing malformed JSON case"])
    payload["how_to_fix"] = _json_text(["Add focused boundary normalization tests"])

    _artifact_type, normalized = prepare_artifact_submission(
        {
            "artifact_type": "planning_analysis_decision",
            "content": _json_text(payload),
        },
        session_drain="planning_analysis",
        base_path=tmp_path,
    )

    assert normalized["what_came_up_short"] == ["missing malformed JSON case"]
    assert normalized["how_to_fix"] == ["Add focused boundary normalization tests"]


def test_artifact_submit_decodes_json_string_for_issues_entries(tmp_path: Path) -> None:
    _artifact_type, normalized = prepare_artifact_submission(
        {
            "artifact_type": "issues",
            "content": _json_text(
                {
                    "status": "issues_found",
                    "summary": "Review found an issue.",
                    "issues": _json_text(
                        [{"path": "x.py", "severity": "medium", "summary": "Needs a test"}]
                    ),
                    "what_came_up_short": _json_text(["Missing coverage"]),
                    "how_to_fix": _json_text(["Add the missing regression test"]),
                }
            ),
        },
        base_path=tmp_path,
    )

    assert normalized["issues"] == [
        {"path": "x.py", "severity": "medium", "summary": "Needs a test"}
    ]


def test_artifact_submit_decodes_json_string_inside_persisted_issues_envelope(
    tmp_path: Path,
) -> None:
    content = {
        "status": "issues_found",
        "summary": "Review found an issue.",
        "issues": [{"path": "x.py", "severity": "medium", "summary": "Needs a test"}],
        "what_came_up_short": ["Missing coverage"],
        "how_to_fix": ["Add the missing regression test"],
    }
    _artifact_type, normalized = prepare_artifact_submission(
        {
            "artifact_type": "issues",
            "content": _json_text({"type": "issues", "content": _json_text(content)}),
        },
        base_path=tmp_path,
    )

    assert normalized["status"] == "issues_found"
    assert normalized["issues"] == [
        {"path": "x.py", "severity": "medium", "summary": "Needs a test"}
    ]


def test_artifact_submit_decodes_json_string_for_commit_cleanup_actions(
    tmp_path: Path,
) -> None:
    _artifact_type, normalized = prepare_artifact_submission(
        {
            "artifact_type": "commit_cleanup",
            "content": _json_text(
                {
                    "analysis_complete": True,
                    "actions": _json_text(
                        [{"action": "add_to_gitignore", "pattern": "/.agent/tmp/"}]
                    ),
                }
            ),
        },
        base_path=tmp_path,
    )

    assert normalized["actions"] == [{"action": "add_to_gitignore", "pattern": "/.agent/tmp/"}]


def test_artifact_submit_decodes_json_string_for_development_continuation(
    tmp_path: Path,
) -> None:
    _artifact_type, normalized = prepare_artifact_submission(
        {
            "artifact_type": "development_result",
            "content": _json_text(
                {
                    "status": "partial",
                    "summary": "Partial progress.",
                    "files_changed": "ralph/mcp/tools/artifact.py",
                    "next_steps": "Resume in prior session.",
                    "continuation": _json_text({"prior_session_id": "session-123"}),
                }
            ),
        },
        base_path=tmp_path,
    )

    assert normalized["continuation"] == {"prior_session_id": "session-123"}


def test_artifact_submit_malformed_json_still_fails(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError, match="Content must be valid JSON"):
        prepare_artifact_submission(
            {
                "artifact_type": "planning_analysis_decision",
                "content": '{"status": "completed",',
            },
            session_drain="planning_analysis",
            base_path=tmp_path,
        )


def test_artifact_submit_missing_required_fields_still_fail(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError, match="summary"):
        prepare_artifact_submission(
            {
                "artifact_type": "planning_analysis_decision",
                "content": {"status": "completed"},
            },
            session_drain="planning_analysis",
            base_path=tmp_path,
        )


def test_artifact_submit_repairs_full_fenced_json_block(tmp_path: Path) -> None:
    payload = _analysis_request_changes_payload()
    content = f"```json\n{_json_text(payload)}\n```"

    _artifact_type, normalized = prepare_artifact_submission(
        {
            "artifact_type": "planning_analysis_decision",
            "content": content,
        },
        session_drain="planning_analysis",
        base_path=tmp_path,
    )

    assert normalized["summary"] == "Plan needs stronger validation coverage."


def test_artifact_submit_repairs_prose_wrapped_single_json_object(tmp_path: Path) -> None:
    _artifact_type, normalized = prepare_artifact_submission(
        {
            "artifact_type": "planning_analysis_decision",
            "content": f"Here is the payload: {_json_text(_analysis_request_changes_payload())}",
        },
        session_drain="planning_analysis",
        base_path=tmp_path,
    )

    assert normalized["summary"] == "Plan needs stronger validation coverage."


def test_artifact_submit_repairs_js_assignment_wrapped_object(tmp_path: Path) -> None:
    content = f"const payload = {_json_text(_analysis_request_changes_payload())};"

    _artifact_type, normalized = prepare_artifact_submission(
        {
            "artifact_type": "planning_analysis_decision",
            "content": content,
        },
        session_drain="planning_analysis",
        base_path=tmp_path,
    )

    assert normalized["summary"] == "Plan needs stronger validation coverage."


def test_artifact_submit_repairs_bare_identifier_keys(tmp_path: Path) -> None:
    content = (
        "{status: 'request_changes', summary: 'Needs work', "
        "what_came_up_short: ['Missing test'], how_to_fix: ['Add test']}"
    )

    _artifact_type, normalized = prepare_artifact_submission(
        {
            "artifact_type": "planning_analysis_decision",
            "content": content,
        },
        session_drain="planning_analysis",
        base_path=tmp_path,
    )

    assert normalized["status"] == "request_changes"
    assert normalized["what_came_up_short"] == ["Missing test"]


def test_artifact_submit_repairs_mixed_single_quotes_and_json_boolean_literals(
    tmp_path: Path,
) -> None:
    _artifact_type, normalized = prepare_artifact_submission(
        {
            "artifact_type": "commit_cleanup",
            "content": "{'analysis_complete': true, 'actions': []}",
        },
        base_path=tmp_path,
    )

    assert normalized["analysis_complete"] is True


def test_artifact_submit_keeps_unambiguous_null_for_schema_validation(
    tmp_path: Path,
) -> None:
    with pytest.raises(InvalidParamsError, match="open_questions"):
        prepare_artifact_submission(
            {
                "artifact_type": "product_spec",
                "content": (
                    "{'title': 'T', 'scope': 'S', 'goals': ['G'], 'users': ['U'], "
                    "'constraints': [], 'success_criteria': ['C'], 'open_questions': null}"
                ),
            },
            base_path=tmp_path,
        )


def test_plan_artifact_submit_repairs_fenced_plan_json(tmp_path: Path) -> None:
    _artifact_type, normalized = prepare_artifact_submission(
        {
            "artifact_type": "plan",
            "content": f"```json\n{_json_text(_valid_plan_payload())}\n```",
        },
        session_drain="planning",
        base_path=tmp_path,
    )

    assert normalized["summary"] == {
        "context": "Harden artifact submission parsing.",
        "scope_items": [{"text": "normalize"}, {"text": "validate"}, {"text": "verify"}],
    }


def test_plan_artifact_submit_decodes_string_content_inside_plan_envelope(
    tmp_path: Path,
) -> None:
    _artifact_type, normalized = prepare_artifact_submission(
        {
            "artifact_type": "plan",
            "content": _json_text({"type": "plan", "content": _json_text(_valid_plan_payload())}),
        },
        session_drain="planning",
        base_path=tmp_path,
    )

    assert normalized["skills_mcp"] == {"skills": ["test-driven-development"]}


def test_plan_step_content_with_json_example_remains_text(tmp_path: Path) -> None:
    plan = _valid_plan_payload()
    steps = cast("list[dict[str, object]]", plan["steps"])
    steps[0]["content"] = 'Set the config to {"enabled": true} and run verification.'

    _artifact_type, normalized = prepare_artifact_submission(
        {
            "artifact_type": "plan",
            "content": _json_text(plan),
        },
        session_drain="planning",
        base_path=tmp_path,
    )

    normalized_steps = cast("list[dict[str, object]]", normalized["steps"])
    assert (
        normalized_steps[0]["content"]
        == 'Set the config to {"enabled": true} and run verification.'
    )


def test_plan_section_submit_unwraps_double_encoded_section_json(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_section(
        _planning_session(),
        workspace,
        {
            "section": "skills_mcp",
            "content": _json_text(_json_text({"skills": ["writing-plans"], "mcps": []})),
        },
    )

    assert result.is_error is False
    sections = _read_draft_sections(tmp_path)
    assert sections["skills_mcp"] == {"skills": ["writing-plans"]}


def test_plan_section_submit_repairs_trailing_commas_and_json_comments(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_section(
        _planning_session(),
        workspace,
        {
            "section": "skills_mcp",
            "content": """
            {
              // Choose the planning skill.
              "skills": ["writing-plans",],
              "mcps": [],
            }
            """,
        },
    )

    assert result.is_error is False
    sections = _read_draft_sections(tmp_path)
    assert sections["skills_mcp"] == {"skills": ["writing-plans"]}


def test_plan_section_submit_repairs_missing_commas_between_properties(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_section(
        _planning_session(),
        workspace,
        {
            "section": "skills_mcp",
            "content": '{"skills": ["writing-plans"] "mcps": []}',
        },
    )

    assert result.is_error is False
    sections = _read_draft_sections(tmp_path)
    assert sections["skills_mcp"] == {"skills": ["writing-plans"]}


def test_plan_section_submit_repairs_missing_commas_between_array_items(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_section(
        _planning_session(),
        workspace,
        {
            "section": "risks_mitigations",
            "content": (
                '[{"risk": "A", "mitigation": "B"} '
                '{"risk": "C", "mitigation": "D"}]'
            ),
        },
    )

    assert result.is_error is False
    sections = _read_draft_sections(tmp_path)
    assert sections["risks_mitigations"] == [
        {"risk": "A", "mitigation": "B"},
        {"risk": "C", "mitigation": "D"},
    ]


def test_plan_section_submit_repairs_smart_quote_delimiters(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_section(
        _planning_session(),
        workspace,
        {
            "section": "skills_mcp",
            "content": "{“skills”: [“writing-plans”], “mcps”: []}",
        },
    )

    assert result.is_error is False
    sections = _read_draft_sections(tmp_path)
    assert sections["skills_mcp"] == {"skills": ["writing-plans"]}


def test_plan_section_submit_repairs_item_wrapped_summary_lists(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_section(
        _planning_session(),
        workspace,
        {
            "section": "summary",
            "content": {
                "context": "Fix the planner staging loop.",
                "scope_items": {
                    "item": [
                        {"text": "Add a wrapper repair regression test", "category": "test"},
                        {"text": "Repair MCP JSON list wrappers", "category": "bugfix"},
                        {"text": "Run focused plan submit tests", "category": "test"},
                    ]
                },
            },
        },
    )

    assert result.is_error is False
    response = cast("dict[str, object]", json.loads(_read_response_text(result)))
    assert response["validation_warnings"] == []
    summary = cast("dict[str, object]", _read_draft_sections(tmp_path)["summary"])
    assert summary["scope_items"] == [
        {"text": "Add a wrapper repair regression test", "category": "test"},
        {"text": "Repair MCP JSON list wrappers", "category": "bugfix"},
        {"text": "Run focused plan submit tests", "category": "test"},
    ]


def test_plan_section_submit_repairs_item_wrapped_skills_mcp_lists(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_section(
        _planning_session(),
        workspace,
        {
            "section": "skills_mcp",
            "content": {
                "skills": {
                    "item": ["submit-plan-artifact", "test-driven-development"],
                },
                "mcps": {"item": "context7"},
            },
        },
    )

    assert result.is_error is False
    response = cast("dict[str, object]", json.loads(_read_response_text(result)))
    assert response["validation_warnings"] == []
    assert _read_draft_sections(tmp_path)["skills_mcp"] == {
        "skills": ["submit-plan-artifact", "test-driven-development"],
        "mcps": ["context7"],
    }


def test_plan_section_submit_repairs_repeated_item_wrapped_skills_mcp_lists(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_section(
        _planning_session(),
        workspace,
        {
            "section": "skills_mcp",
            "content": {
                "skills": {"item": {"item": ["submit-plan-artifact"]}},
                "mcps": {"item": {"item": "context7"}},
            },
        },
    )

    assert result.is_error is False
    response = cast("dict[str, object]", json.loads(_read_response_text(result)))
    assert response["validation_warnings"] == []
    assert _read_draft_sections(tmp_path)["skills_mcp"] == {
        "skills": ["submit-plan-artifact"],
        "mcps": ["context7"],
    }


def test_plan_section_submit_repairs_nested_item_wrappers_in_design(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_section(
        _planning_session(),
        workspace,
        {
            "section": "design",
            "content": {
                "acceptance_criteria": {
                    "criteria": {
                        "item": [
                            {
                                "id": "AC-01",
                                "description": "The planner stages wrapped list fields.",
                                "satisfied_by_steps": {"item": 1},
                            }
                        ]
                    }
                },
                "drift_detection": {
                    "guard_commands": {"item": "pytest tests/test_x.py -q"},
                    "expected_outputs": {"item": ["1 passed"]},
                    "sources": {"item": "ci"},
                    "on_drift_action": "fail-verify",
                },
                "testability": {
                    "must_be_black_box": True,
                    "required_test_layers": {"item": ["unit", "integration"]},
                    "clock_injection_required": True,
                },
            },
        },
    )

    assert result.is_error is False
    response = cast("dict[str, object]", json.loads(_read_response_text(result)))
    assert response["validation_warnings"] == []
    design = cast("dict[str, object]", _read_draft_sections(tmp_path)["design"])
    acceptance = cast("dict[str, object]", design["acceptance_criteria"])
    assert acceptance["criteria"] == [
        {
            "id": "AC-01",
            "description": "The planner stages wrapped list fields.",
            "satisfied_by_steps": [1],
        }
    ]
    drift = cast("dict[str, object]", design["drift_detection"])
    assert drift["guard_commands"] == ["pytest tests/test_x.py -q"]
    assert drift["sources"] == ["ci"]


def test_plan_section_submit_repairs_python_literal_container_text(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_section(
        _planning_session(),
        workspace,
        {
            "section": "risks_mitigations",
            "content": (
                "{'risks_mitigations': "
                "[{'risk': 'Schema drift', 'mitigation': 'Keep validation tests'}]}"
            ),
        },
    )

    assert result.is_error is False
    sections = _read_draft_sections(tmp_path)
    assert sections["risks_mitigations"] == [
        {"risk": "Schema drift", "mitigation": "Keep validation tests"}
    ]


def test_plan_section_python_literal_set_still_fails(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)

    with pytest.raises(InvalidParamsError, match="Content must be valid JSON"):
        handle_submit_plan_section(
            _planning_session(),
            workspace,
            {"section": "skills_mcp", "content": "{'skills': {'writing-plans'}, 'mcps': []}"},
        )


def test_plan_section_submit_unwraps_section_named_object_wrapper(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_section(
        _planning_session(),
        workspace,
        {
            "section": "critical_files",
            "content": {
                "critical_files": {"primary_files": [{"path": "x.py", "action": "modify"}]}
            },
        },
    )

    assert result.is_error is False
    sections = _read_draft_sections(tmp_path)
    assert sections["critical_files"] == {"primary_files": [{"path": "x.py", "action": "modify"}]}


def test_plan_section_submit_decodes_wrapped_json_string_for_list_section(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_section(
        _planning_session(),
        workspace,
        {
            "section": "risks_mitigations",
            "content": _json_text(
                {
                    "risks_mitigations": _json_text(
                        [{"risk": "Schema drift", "mitigation": "Keep validation tests"}]
                    )
                }
            ),
        },
    )

    assert result.is_error is False
    sections = _read_draft_sections(tmp_path)
    assert sections["risks_mitigations"] == [
        {"risk": "Schema drift", "mitigation": "Keep validation tests"}
    ]


def test_plan_sections_batch_normalizes_each_entry_before_validation(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)
    result = handle_submit_plan_sections(
        _planning_session(),
        workspace,
        {
            "entries": [
                {
                    "section": "skills_mcp",
                    "content": _json_text(
                        {"skills_mcp": _json_text({"skills": ["writing-plans"], "mcps": []})}
                    ),
                },
                {
                    "section": "risks_mitigations",
                    "content": _json_text(
                        _json_text(
                            [{"risk": "Bad container", "mitigation": "Normalize at boundary"}]
                        )
                    ),
                },
            ]
        },
    )

    assert result.is_error is False
    response = cast("dict[str, object]", json.loads(_read_response_text(result)))
    assert response.get("submitted") == ["skills_mcp", "risks_mitigations"]


def test_plan_section_malformed_json_still_fails(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)

    with pytest.raises(InvalidParamsError, match="Content must be valid JSON"):
        handle_submit_plan_section(
            _planning_session(),
            workspace,
            {"section": "skills_mcp", "content": '{"skills": ["writing-plans"],'},
        )


def test_plan_section_concatenated_json_still_fails(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)

    with pytest.raises(InvalidParamsError, match="Content must be valid JSON"):
        handle_submit_plan_section(
            _planning_session(),
            workspace,
            {
                "section": "skills_mcp",
                "content": (
                    '{"skills": ["writing-plans"], "mcps": []}'
                    '{"skills": ["verification-before-completion"], "mcps": []}'
                ),
            },
        )


def test_plan_section_missing_required_field_stages_then_fails_validation(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)

    result = handle_submit_plan_section(
        _planning_session(),
        workspace,
        {"section": "critical_files", "content": {"reference_files": []}},
    )

    assert result.is_error is False
    payload = json.loads(cast("ToolContent", result.content[0]).text)
    warnings = cast("list[str]", payload["validation_warnings"])
    assert any("primary_files" in warning for warning in warnings)

    validation = handle_validate_plan_draft(_planning_session(), workspace, {})
    assert validation.is_error is False
    assert "primary_files" in cast("ToolContent", validation.content[0]).text
