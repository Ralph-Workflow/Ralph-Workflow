"""Tests for ralph/mcp/tool_artifact.py — MCP artifact submission handlers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from ralph.mcp.tools.artifact import (
    ArtifactHandlerDeps,
    handle_discard_plan_draft,
    handle_finalize_plan,
    handle_get_plan_draft,
    handle_submit_artifact,
    handle_submit_plan_section,
)
from ralph.mcp.tools.coordination import InvalidParamsError, ToolContent
from tests.plan_fixtures import DEFAULT_SKILLS_MCP
from tests.test_tool_artifact_2_helper_failingartifactbackend import FailingArtifactBackend
from tests.test_tool_artifact_2_helper_memorybackend import MemoryBackend
from tests.test_tool_artifact_2_helper_mocksession import MockSession
from tests.test_tool_artifact_2_helper_mockworkspace import MockWorkspace


def _memory_handler_deps(backend: MemoryBackend) -> ArtifactHandlerDeps:
    return ArtifactHandlerDeps(backend=backend, now_iso=lambda: "2026-04-15T12:00:00+00:00")


@dataclass
class _Content:
    value: dict[str, object]


def _content(value: dict[str, object]) -> str:
    return json.dumps(value)


def _result_json(result: object) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(cast("ToolContent", result.content[0]).text))


def _full_plan_payload() -> dict[str, object]:
    return {
        "summary": {
            "context": "Test context for unit tests.",
            "scope_items": [
                {"text": "Scope item one"},
                {"text": "Scope item two"},
                {"text": "Scope item three"},
            ],
        },
        "skills_mcp": DEFAULT_SKILLS_MCP,
        "steps": [{"number": 1, "title": "Test step", "content": "Do the thing"}],
        "critical_files": {
            "primary_files": [{"path": "test.py", "action": "modify"}],
            "reference_files": [],
        },
        "risks_mitigations": [{"risk": "Test risk", "mitigation": "Test mitigation"}],
        "verification_strategy": [{"method": "pytest", "expected_outcome": "all tests pass"}],
    }


def _submit_required_plan_sections(tmp_path: Path, plan: dict[str, object]) -> None:
    _submit_section(tmp_path, "summary", plan["summary"])
    _submit_section(tmp_path, "skills_mcp", plan["skills_mcp"])
    _submit_section(tmp_path, "steps", plan["steps"])
    _submit_section(tmp_path, "critical_files", plan["critical_files"])
    _submit_section(tmp_path, "risks_mitigations", plan["risks_mitigations"])
    _submit_section(tmp_path, "verification_strategy", plan["verification_strategy"])


def _submit_section(
    tmp_path: Path,
    section: str,
    content: object,
    *,
    mode: str = "replace",
) -> None:
    params: dict[str, object] = {
        "section": section,
        "content": json.dumps(content) if not isinstance(content, str) else content,
        "mode": mode,
    }
    handle_submit_plan_section(MockSession(), MockWorkspace(tmp_path), params)


def test_handle_submit_artifact_invalid_development_analysis_decision_points_to_format_doc(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        InvalidParamsError,
        match=r"\.agent/artifact-formats/development_analysis_decision\.md",
    ):
        handle_submit_artifact(
            MockSession(drain="development_analysis"),
            MockWorkspace(tmp_path),
            {
                "artifact_type": "development_analysis_decision",
                "content": _content({"garbage": "value"}),
            },
        )
    assert (tmp_path / ".agent" / "artifact-formats" / "development_analysis_decision.md").exists()
    content = (
        tmp_path / ".agent" / "artifact-formats" / "development_analysis_decision.md"
    ).read_text(encoding="utf-8")
    assert content.startswith("# development_analysis_decision artifact format")


def test_handle_submit_artifact_invalid_review_analysis_decision_points_to_format_doc(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        InvalidParamsError,
        match=r"\.agent/artifact-formats/review_analysis_decision\.md",
    ):
        handle_submit_artifact(
            MockSession(drain="review_analysis"),
            MockWorkspace(tmp_path),
            {
                "artifact_type": "review_analysis_decision",
                "content": _content({"garbage": "value"}),
            },
        )
    assert (tmp_path / ".agent" / "artifact-formats" / "review_analysis_decision.md").exists()
    content = (tmp_path / ".agent" / "artifact-formats" / "review_analysis_decision.md").read_text(
        encoding="utf-8"
    )
    assert content.startswith("# review_analysis_decision artifact format")


def test_handle_submit_artifact_accepts_generic_planning_analysis_decision_and_mirrors_handoff(
    tmp_path: Path,
) -> None:
    result = handle_submit_artifact(
        MockSession(drain="planning_analysis"),
        MockWorkspace(tmp_path),
        {
            "artifact_type": "analysis_decision",
            "content": _content(
                {
                    "status": "request_changes",
                    "summary": "The plan is missing executable verification steps.",
                    "what_came_up_short": [
                        "Verification strategy does not include exact commands."
                    ],
                    "how_to_fix": [
                        "Add explicit commands and expected outcomes for each verification step."
                    ],
                }
            ),
        },
    )

    assert result.is_error is False
    decision_md = (tmp_path / ".agent" / "PLANNING_ANALYSIS_DECISION.md").read_text(
        encoding="utf-8"
    )
    assert "# Planning Analysis Decision" in decision_md
    assert "The plan is missing executable verification steps." in decision_md
    assert "Add explicit commands and expected outcomes for each verification step." in decision_md


def test_handle_submit_artifact_invalid_planning_analysis_decision_points_to_format_doc(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        InvalidParamsError,
        match=r"\.agent/artifact-formats/planning_analysis_decision\.md",
    ):
        handle_submit_artifact(
            MockSession(drain="planning_analysis"),
            MockWorkspace(tmp_path),
            {
                "artifact_type": "planning_analysis_decision",
                "content": _content({"garbage": "value"}),
            },
        )
    assert (tmp_path / ".agent" / "artifact-formats" / "planning_analysis_decision.md").exists()
    content = (
        tmp_path / ".agent" / "artifact-formats" / "planning_analysis_decision.md"
    ).read_text(encoding="utf-8")
    assert content.startswith("# planning_analysis_decision artifact format")


def test_plan_validation_error_is_redirected_through_format_doc(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError, match=r"artifact-formats/plan\.md"):
        handle_submit_artifact(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "artifact_type": "plan",
                "content": _content(
                    {
                        "summary": {
                            "context": "Too short.",
                            "scope_items": [
                                {"text": "One"},
                                {"text": "Two"},
                                {"text": "Three"},
                            ],
                        },
                        "steps": [
                            {"number": 1, "title": "Missing sections", "content": "No verify"}
                        ],
                        "critical_files": {"primary_files": [{"path": "x", "action": "modify"}]},
                        "risks_mitigations": [{"risk": "Oops", "mitigation": "Fix it"}],
                    }
                ),
            },
        )
    assert (tmp_path / ".agent" / "artifact-formats" / "plan.md").exists()


def test_format_doc_materialization_failure_still_raises_pointer_error(tmp_path: Path) -> None:
    format_doc_path = tmp_path / ".agent" / "artifact-formats" / "commit_message.md"
    backend = FailingArtifactBackend(format_doc_path, message="read-only workspace")
    deps = _memory_handler_deps(backend)

    with pytest.raises(InvalidParamsError) as exc_info:
        handle_submit_artifact(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "artifact_type": "commit_message",
                "content": _content({"message": "fix: old format"}),
            },
            deps=deps,
        )

    error_msg = str(exc_info.value)
    assert "commit_message" in error_msg
    assert "could not write the reference file" in error_msg
    assert not format_doc_path.exists()


def test_piecewise_plan_submission_produces_same_plan_json_as_atomic(tmp_path: Path) -> None:
    plan = _full_plan_payload()
    atomic_path = tmp_path / "atomic"
    piecewise_path = tmp_path / "piecewise"

    handle_submit_artifact(
        MockSession(),
        MockWorkspace(atomic_path),
        {"artifact_type": "plan", "content": _content(plan)},
    )

    _submit_required_plan_sections(piecewise_path, plan)
    result = handle_finalize_plan(MockSession(), MockWorkspace(piecewise_path), {})

    assert result.is_error is False
    atomic_plan_file = atomic_path / ".agent" / "artifacts" / "plan.json"
    plan_file = piecewise_path / ".agent" / "artifacts" / "plan.json"
    atomic_stored = json.loads(atomic_plan_file.read_text(encoding="utf-8"))
    stored = json.loads(plan_file.read_text(encoding="utf-8"))
    for artifact in (atomic_stored, stored):
        artifact.pop("created_at", None)
        artifact.pop("updated_at", None)
    assert stored == atomic_stored
    assert stored["type"] == "plan"
    summary = cast("dict[str, object]", stored["content"]["summary"])
    assert summary["context"] == "Test context for unit tests."
    assert (
        (atomic_path / ".agent" / "PLAN.md")
        .read_text(encoding="utf-8")
        .startswith("# Execution Plan\n")
    )
    assert (
        (piecewise_path / ".agent" / "PLAN.md")
        .read_text(encoding="utf-8")
        .startswith("# Execution Plan\n")
    )
    # Draft must be gone after a successful finalize.
    assert not (piecewise_path / ".agent" / "artifacts" / ".plan_draft.json").exists()


def test_submit_plan_section_stages_invalid_section_payload_with_warning(
    tmp_path: Path,
) -> None:
    result = handle_submit_plan_section(
        MockSession(),
        MockWorkspace(tmp_path),
        {
            "section": "summary",
            "content": _content({"context": "too short", "scope_items": [{"text": "only one"}]}),
        },
    )

    payload = _result_json(result)
    warnings = cast("list[str]", payload["validation_warnings"])
    assert result.is_error is False
    assert any("[summary]" in warning for warning in warnings)


def test_submit_plan_section_rejects_unknown_section(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError, match="Unknown plan section"):
        handle_submit_plan_section(
            MockSession(),
            MockWorkspace(tmp_path),
            {"section": "bogus", "content": _content({})},
        )


def test_submit_plan_section_skills_mcp_error_explains_mcps_shape_and_doc(tmp_path: Path) -> None:
    result = handle_submit_plan_section(
        MockSession(),
        MockWorkspace(tmp_path),
        {
            "section": "skills_mcp",
            "content": _content({"skills": ["writing-plans"], "mcps": "docs-mcp"}),
        },
    )

    payload = _result_json(result)
    warnings = cast("list[str]", payload["validation_warnings"])
    assert result.is_error is False
    assert any("mcps" in warning for warning in warnings)


def test_submit_plan_section_skills_mcp_error_explains_skills_shape_and_doc(tmp_path: Path) -> None:
    result = handle_submit_plan_section(
        MockSession(),
        MockWorkspace(tmp_path),
        {
            "section": "skills_mcp",
            "content": _content({"mcps": []}),
        },
    )

    payload = _result_json(result)
    warnings = cast("list[str]", payload["validation_warnings"])
    assert result.is_error is False
    assert any("skills" in warning for warning in warnings)


def test_submit_plan_section_summary_error_explains_expected_shape(tmp_path: Path) -> None:
    result = handle_submit_plan_section(
        MockSession(),
        MockWorkspace(tmp_path),
        {"section": "summary", "content": _content({"context": "ctx"})},
    )

    payload = _result_json(result)
    warnings = cast("list[str]", payload["validation_warnings"])
    assert result.is_error is False
    assert any("scope_items" in warning for warning in warnings)


def test_submit_plan_section_steps_replace_error_explains_array_shape_and_doc(
    tmp_path: Path,
) -> None:
    with pytest.raises(InvalidParamsError) as exc_info:
        handle_submit_plan_section(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "section": "steps",
                "content": _content(
                    {
                        "number": 1,
                        "title": "One",
                        "content": "single object instead of list",
                    }
                ),
            },
        )

    message = str(exc_info.value)
    assert ".agent/artifact-formats/plan.md" in message
    assert "section 'steps' with mode='replace' must be a JSON array" in message


def test_submit_plan_section_critical_files_error_explains_primary_files_shape(
    tmp_path: Path,
) -> None:
    result = handle_submit_plan_section(
        MockSession(),
        MockWorkspace(tmp_path),
        {"section": "critical_files", "content": _content({"reference_files": []})},
    )

    payload = _result_json(result)
    warnings = cast("list[str]", payload["validation_warnings"])
    assert result.is_error is False
    assert any("primary_files" in warning for warning in warnings)


def test_submit_plan_section_risks_array_error_explains_expected_shape(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError) as exc_info:
        handle_submit_plan_section(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "section": "risks_mitigations",
                "content": _content({"risk": "r", "mitigation": "m"}),
            },
        )

    message = str(exc_info.value)
    assert "section 'risks_mitigations' with mode='replace' must be a JSON array" in message


def test_submit_plan_section_verification_strategy_error_explains_expected_shape(
    tmp_path: Path,
) -> None:
    with pytest.raises(InvalidParamsError) as exc_info:
        handle_submit_plan_section(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "section": "verification_strategy",
                "content": _content({"method": "pytest", "expected_outcome": "passes"}),
            },
        )

    message = str(exc_info.value)
    assert "section 'verification_strategy' with mode='replace' must be a JSON array" in message


def test_submit_plan_section_invalid_json_explains_fix_and_doc(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError) as exc_info:
        handle_submit_plan_section(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "section": "steps",
                "content": '[{"number": 1, "title": "One", "content": "missing close"',
            },
        )

    message = str(exc_info.value)
    assert ".agent/artifact-formats/plan.md" in message
    assert "Content must be valid JSON" in message
    assert 'Expected shape for section "steps"' in message
    assert '[{"number":1' in message
    assert "{'" not in message


def test_submit_plan_section_missing_section_includes_fix_guidance(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError) as exc_info:
        handle_submit_plan_section(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "content": _content(
                    {
                        "context": "ctx",
                        "scope_items": [
                            {"text": "a"},
                            {"text": "b"},
                            {"text": "c"},
                        ],
                    }
                )
            },
        )

    message = str(exc_info.value)
    assert ".agent/artifact-formats/plan.md" in message
    assert "Missing 'section' parameter" in message
    assert "ralph_submit_plan_section" in message


def test_submit_plan_section_invalid_mode_includes_fix_guidance(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError) as exc_info:
        handle_submit_plan_section(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "section": "summary",
                "mode": "merge",
                "content": _content(
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
        )

    message = str(exc_info.value)
    assert ".agent/artifact-formats/plan.md" in message
    assert "'mode' must be 'replace' or 'append'" in message
    assert "section='summary'" in message


def test_submit_plan_section_append_accepts_list_payload_for_steps(tmp_path: Path) -> None:
    _submit_section(
        tmp_path,
        "summary",
        {"context": "ctx", "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}]},
    )
    _submit_section(tmp_path, "skills_mcp", {"skills": ["writing-plans"], "mcps": []})
    _submit_section(
        tmp_path,
        "steps",
        [
            {"number": 1, "title": "First", "content": "first"},
            {"number": 2, "title": "Second", "content": "second"},
        ],
        mode="append",
    )

    draft = json.loads(
        (tmp_path / ".agent" / "artifacts" / ".plan_draft.json").read_text(encoding="utf-8")
    )
    steps = cast("list[dict[str, object]]", cast("dict[str, object]", draft["sections"])["steps"])
    assert [cast("str", step["title"]) for step in steps] == ["First", "Second"]


def test_submit_artifact_invalid_development_result_status_includes_doc_guidance(
    tmp_path: Path,
) -> None:
    with pytest.raises(InvalidParamsError) as exc_info:
        handle_submit_artifact(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "artifact_type": "development_result",
                "content": _content(
                    {
                        "status": "blocked",
                        "summary": "bad status",
                        "files_changed": "- src/example.py",
                    }
                ),
            },
        )

    message = str(exc_info.value)
    assert ".agent/artifact-formats/development_result.md" in message
    assert "status" in message and "completed" in message and "partial" in message
    assert "ralph_submit_artifact" in message


def test_finalize_plan_fails_when_required_section_missing(tmp_path: Path) -> None:
    plan = _full_plan_payload()
    _submit_section(tmp_path, "summary", plan["summary"])
    _submit_section(tmp_path, "steps", plan["steps"])
    _submit_section(tmp_path, "critical_files", plan["critical_files"])
    _submit_section(tmp_path, "risks_mitigations", plan["risks_mitigations"])

    with pytest.raises(InvalidParamsError, match="skills_mcp"):
        handle_finalize_plan(MockSession(), MockWorkspace(tmp_path), {})

    # Draft survives so the agent can fix and retry.
    assert (tmp_path / ".agent" / "artifacts" / ".plan_draft.json").exists()


def test_finalize_plan_fails_when_no_draft(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError, match="No plan draft"):
        handle_finalize_plan(MockSession(), MockWorkspace(tmp_path), {})


def test_submit_plan_section_append_mode_extends_steps_list(tmp_path: Path) -> None:
    plan = _full_plan_payload()
    _submit_section(tmp_path, "summary", plan["summary"])
    _submit_section(tmp_path, "skills_mcp", plan["skills_mcp"])
    _submit_section(
        tmp_path,
        "steps",
        {"number": 1, "title": "one", "content": "first step"},
        mode="append",
    )
    _submit_section(
        tmp_path,
        "steps",
        {"number": 2, "title": "two", "content": "second step"},
        mode="append",
    )
    _submit_section(tmp_path, "critical_files", plan["critical_files"])
    _submit_section(tmp_path, "risks_mitigations", plan["risks_mitigations"])
    _submit_section(tmp_path, "verification_strategy", plan["verification_strategy"])
    handle_finalize_plan(MockSession(), MockWorkspace(tmp_path), {})

    stored = json.loads(
        (tmp_path / ".agent" / "artifacts" / "plan.json").read_text(encoding="utf-8")
    )
    steps = cast("list[dict[str, object]]", stored["content"]["steps"])
    assert [step["number"] for step in steps] == [1, 2]


def test_get_plan_draft_reports_staged_sections(tmp_path: Path) -> None:
    plan = _full_plan_payload()
    _submit_section(tmp_path, "summary", plan["summary"])
    _submit_section(tmp_path, "steps", plan["steps"])

    result = handle_get_plan_draft(MockSession(), MockWorkspace(tmp_path), {})

    payload = json.loads(cast("ToolContent", result.content[0]).text)
    assert sorted(payload["staged_sections"]) == ["steps", "summary"]


def test_get_plan_draft_when_absent_returns_empty_list(tmp_path: Path) -> None:
    result = handle_get_plan_draft(MockSession(), MockWorkspace(tmp_path), {})
    payload = json.loads(cast("ToolContent", result.content[0]).text)
    assert payload == {"staged_sections": []}


def test_get_plan_draft_hydrates_from_existing_plan_artifact(tmp_path: Path) -> None:
    plan = _full_plan_payload()
    handle_submit_artifact(
        MockSession(),
        MockWorkspace(tmp_path),
        {"artifact_type": "plan", "content": _content(plan)},
    )

    result = handle_get_plan_draft(MockSession(), MockWorkspace(tmp_path), {})

    payload = cast("dict[str, object]", json.loads(cast("ToolContent", result.content[0]).text))
    draft_payload = cast("dict[str, object]", payload["draft"])
    draft_summary = cast("dict[str, object]", draft_payload["summary"])
    plan_summary = cast("dict[str, object]", plan["summary"])
    staged_sections = cast("list[str]", payload["staged_sections"])
    assert sorted(staged_sections) == [
        "critical_files",
        "risks_mitigations",
        "skills_mcp",
        "steps",
        "summary",
        "verification_strategy",
    ]
    assert payload["source"] == "finalized_plan"
    assert draft_summary["context"] == plan_summary["context"]


def test_get_plan_draft_prefers_newer_finalized_plan_over_older_draft(tmp_path: Path) -> None:
    plan = _full_plan_payload()
    handle_submit_artifact(
        MockSession(),
        MockWorkspace(tmp_path),
        {"artifact_type": "plan", "content": _content(plan)},
    )
    draft_path = tmp_path / ".agent" / "artifacts" / ".plan_draft.json"
    draft_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "started_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "sections": {
                    "summary": {
                        "context": "Older stale draft.",
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = handle_get_plan_draft(MockSession(), MockWorkspace(tmp_path), {})

    payload = cast("dict[str, object]", json.loads(cast("ToolContent", result.content[0]).text))
    draft_payload = cast("dict[str, object]", payload["draft"])
    draft_summary = cast("dict[str, object]", draft_payload["summary"])
    plan_summary = cast("dict[str, object]", plan["summary"])
    assert payload["source"] == "finalized_plan"
    assert draft_summary["context"] == plan_summary["context"]


def test_discard_plan_draft_deletes_draft_file(tmp_path: Path) -> None:
    plan = _full_plan_payload()
    _submit_section(tmp_path, "summary", plan["summary"])
    draft_path = tmp_path / ".agent" / "artifacts" / ".plan_draft.json"
    assert draft_path.exists()

    handle_discard_plan_draft(MockSession(), MockWorkspace(tmp_path), {})

    assert not draft_path.exists()


def test_plan_draft_handlers_support_injected_persistence_without_real_filesystem() -> None:
    backend = MemoryBackend()
    workspace = MockWorkspace(Path("/virtual-plan"))
    plan = _full_plan_payload()
    deps = _memory_handler_deps(backend)

    for section in [
        "summary",
        "skills_mcp",
        "steps",
        "critical_files",
        "risks_mitigations",
        "verification_strategy",
    ]:
        section_payload = plan[section]
        params: dict[str, object] = {
            "section": section,
            "content": _content(cast("dict[str, object]", section_payload))
            if isinstance(section_payload, dict)
            else json.dumps(section_payload),
        }
        handle_submit_plan_section(MockSession(), workspace, params, deps=deps)

    draft_result = handle_get_plan_draft(MockSession(), workspace, {}, deps=deps)
    draft_payload = json.loads(cast("ToolContent", draft_result.content[0]).text)
    assert sorted(draft_payload["staged_sections"]) == [
        "critical_files",
        "risks_mitigations",
        "skills_mcp",
        "steps",
        "summary",
        "verification_strategy",
    ]

    finalize_result = handle_finalize_plan(MockSession(), workspace, {}, deps=deps)
    assert finalize_result.is_error is False
    stored_plan = json.loads(backend.read_text(Path("/virtual-plan/.agent/artifacts/plan.json")))
    assert stored_plan["type"] == "plan"
    assert backend.exists(Path("/virtual-plan/.agent/artifacts/.plan_draft.json")) is False


def test_full_plan_submission_clears_existing_draft(tmp_path: Path) -> None:
    plan = _full_plan_payload()
    _submit_section(tmp_path, "summary", plan["summary"])
    draft_path = tmp_path / ".agent" / "artifacts" / ".plan_draft.json"
    assert draft_path.exists()

    handle_submit_artifact(
        MockSession(),
        MockWorkspace(tmp_path),
        {"artifact_type": "plan", "content": _content(plan)},
    )

    assert not draft_path.exists()
    assert (tmp_path / ".agent" / "artifacts" / "plan.json").exists()


def test_submit_plan_section_can_edit_existing_finalized_plan_without_resubmitting_everything(
    tmp_path: Path,
) -> None:
    plan = _full_plan_payload()
    handle_submit_artifact(
        MockSession(),
        MockWorkspace(tmp_path),
        {"artifact_type": "plan", "content": _content(plan)},
    )

    updated_verification = [
        {
            "method": "uv run pytest -q tests/test_tool_artifact.py",
            "expected_outcome": "planning artifact edit flow passes",
        }
    ]
    _submit_section(tmp_path, "verification_strategy", updated_verification)
    result = handle_finalize_plan(MockSession(), MockWorkspace(tmp_path), {})

    assert result.is_error is False
    stored = cast(
        "dict[str, object]",
        json.loads((tmp_path / ".agent" / "artifacts" / "plan.json").read_text(encoding="utf-8")),
    )
    stored_content = cast("dict[str, object]", stored["content"])
    stored_summary = cast("dict[str, object]", stored_content["summary"])
    plan_summary = cast("dict[str, object]", plan["summary"])
    assert stored_summary["context"] == plan_summary["context"]
    assert stored_content["verification_strategy"] == updated_verification


def test_submit_plan_section_can_edit_work_units_on_existing_finalized_plan(
    tmp_path: Path,
) -> None:
    plan = _full_plan_payload()
    plan["work_units"] = [
        {
            "unit_id": "api",
            "description": "Update API handlers",
            "allowed_directories": ["src/api/"],
            "dependencies": [],
        }
    ]
    handle_submit_artifact(
        MockSession(),
        MockWorkspace(tmp_path),
        {"artifact_type": "plan", "content": _content(plan)},
    )

    updated_work_units = [
        {
            "unit_id": "api",
            "description": "Update API and contract tests",
            "allowed_directories": ["src/api/", "tests/api/"],
            "dependencies": [],
        }
    ]
    _submit_section(tmp_path, "work_units", updated_work_units)
    result = handle_finalize_plan(MockSession(), MockWorkspace(tmp_path), {})

    assert result.is_error is False
    stored = json.loads(
        (tmp_path / ".agent" / "artifacts" / "plan.json").read_text(encoding="utf-8")
    )
    assert stored["content"]["work_units"] == [
        {
            "unit_id": "api",
            "description": "Update API and contract tests",
            "allowed_directories": ["src/api/", "tests/api/"],
        }
    ]
