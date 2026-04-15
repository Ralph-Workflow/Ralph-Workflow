"""Tests for ralph/mcp/tool_artifact.py — MCP artifact submission handlers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import pytest

from ralph.mcp.tool_artifact import _prepare_artifact_submission, handle_submit_artifact
from ralph.mcp.tool_coordination import InvalidParamsError

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class MockSession:
    session_id: str = "test-session"

    def check_capability(self, capability: str) -> object:
        return capability == "artifact.submit"


class MockWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def absolute_path(self, path: str) -> str:
        return str(self.root / path)


def _content(value: dict[str, object]) -> str:
    return json.dumps(value)


def test_prepare_artifact_submission_normalizes_plan_without_workspace_io() -> None:
    artifact_type, parsed_content = _prepare_artifact_submission(
        {
            "artifact_type": "plan",
            "content": _content(
                {
                    "summary": {
                        "context": "Plan MCP rollout.",
                        "scope_items": [
                            {"text": "Update validation"},
                            {"text": "Add tests"},
                            {"text": "Update prompts"},
                        ],
                    },
                    "steps": [{"number": 1, "title": "Validate", "content": "Do the work"}],
                    "critical_files": {
                        "primary_files": [
                            {"path": "ralph/mcp/tool_artifact.py", "action": "modify"}
                        ]
                    },
                    "risks_mitigations": [{"risk": "Schema drift", "mitigation": "Add tests"}],
                    "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
                }
            ),
        }
    )

    assert artifact_type == "plan"
    summary = cast("dict[str, object]", parsed_content["summary"])
    assert summary["context"] == "Plan MCP rollout."


def test_prepare_artifact_submission_rejects_legacy_commit_format_without_workspace_io() -> None:
    with pytest.raises(InvalidParamsError, match="structured commit_message schema"):
        _prepare_artifact_submission(
            {
                "artifact_type": "commit_message",
                "content": _content({"message": "fix: legacy format"}),
            }
        )


def test_handle_submit_artifact_accepts_structured_commit_message_payload(tmp_path: Path) -> None:
    result = handle_submit_artifact(
        MockSession(),
        MockWorkspace(tmp_path),
        {
            "artifact_type": "commit_message",
            "content": _content(
                {
                    "type": "commit",
                    "subject": "fix(auth): prevent token expiry race",
                    "body": "Explain why the auth module changed.",
                    "files": ["src/auth/token.py"],
                    "excluded_files": [{"path": "notes/todo.md", "reason": "not_task_related"}],
                }
            ),
        },
    )

    assert result.is_error is False
    artifact_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
    text_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
    stored = json.loads(artifact_file.read_text(encoding="utf-8"))
    assert stored["type"] == "commit_message"
    assert stored["content"] == {
        "type": "commit",
        "subject": "fix(auth): prevent token expiry race",
        "body": "Explain why the auth module changed.",
        "files": ["src/auth/token.py"],
        "excluded_files": [{"path": "notes/todo.md", "reason": "not_task_related"}],
    }
    assert (
        text_file.read_text(encoding="utf-8")
        == "fix(auth): prevent token expiry race\n\nExplain why the auth module changed."
    )


def test_handle_submit_artifact_accepts_structured_skip_payload(tmp_path: Path) -> None:
    handle_submit_artifact(
        MockSession(),
        MockWorkspace(tmp_path),
        {
            "artifact_type": "commit_message",
            "content": _content({"type": "skip", "reason": "No repo changes to commit"}),
        },
    )

    text_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
    assert text_file.read_text(encoding="utf-8") == "SKIP: No repo changes to commit"


def test_handle_submit_artifact_rejects_legacy_message_only_payload(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError, match="must use the structured commit_message schema"):
        handle_submit_artifact(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "artifact_type": "commit_message",
                "content": _content({"message": "fix: old format"}),
            },
        )


def test_handle_submit_artifact_rejects_commit_payload_without_subject(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError, match="require a non-empty 'subject'"):
        handle_submit_artifact(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "artifact_type": "commit_message",
                "content": _content({"type": "commit", "body": "Missing subject"}),
            },
        )


def test_handle_submit_artifact_rejects_body_and_detailed_fields_together(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError, match="Use either 'body' or the detailed body fields"):
        handle_submit_artifact(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "artifact_type": "commit_message",
                "content": _content(
                    {
                        "type": "commit",
                        "subject": "fix: normalize commit validation",
                        "body": "Simple body",
                        "body_summary": "Detailed summary",
                    }
                ),
            },
        )


def test_handle_submit_artifact_accepts_structured_plan_payload(tmp_path: Path) -> None:
    result = handle_submit_artifact(
        MockSession(),
        MockWorkspace(tmp_path),
        {
            "artifact_type": "plan",
            "content": _content(
                {
                    "summary": {
                        "context": "Plan the MCP validation rollout.",
                        "scope_items": [
                            {
                                "text": "Update MCP validation",
                                "count": "2 files",
                                "category": "file_change",
                            },
                            {"text": "Add tests", "count": "3 tests", "category": "test"},
                            {"text": "Update prompts", "count": "1 template", "category": "prompt"},
                        ],
                    },
                    "steps": [
                        {
                            "number": 1,
                            "step_type": "file_change",
                            "priority": "high",
                            "title": "Validate incoming plan payloads",
                            "targets": [{"path": "ralph/mcp/tool_artifact.py", "action": "modify"}],
                            "content": "Reject malformed plans before they are written.",
                        }
                    ],
                    "critical_files": {
                        "primary_files": [
                            {"path": "ralph/mcp/tool_artifact.py", "action": "modify"}
                        ]
                    },
                    "risks_mitigations": [
                        {
                            "severity": "medium",
                            "risk": "Prompt/server drift returns.",
                            "mitigation": "Add HTTP MCP tests.",
                        }
                    ],
                    "verification_strategy": [
                        {
                            "method": "pytest tests/test_mcp_server.py",
                            "expected_outcome": "Plan schema enforced through MCP.",
                        }
                    ],
                }
            ),
        },
    )

    assert result.is_error is False
    artifact_file = tmp_path / ".agent" / "artifacts" / "plan.json"
    stored = json.loads(artifact_file.read_text(encoding="utf-8"))
    assert stored["type"] == "plan"
    assert stored["content"]["summary"]["context"] == "Plan the MCP validation rollout."


def test_handle_submit_artifact_rejects_plan_without_required_sections(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError, match="verification_strategy"):
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


def test_handle_submit_artifact_accepts_structured_development_result(tmp_path: Path) -> None:
    result = handle_submit_artifact(
        MockSession(),
        MockWorkspace(tmp_path),
        {
            "artifact_type": "development_result",
            "content": _content(
                {
                    "status": "completed",
                    "summary": "Implemented MCP capability enforcement.",
                    "files_changed": "- ralph/mcp/tool_bridge.py\n- ralph/mcp/session_bridge.py",
                }
            ),
        },
    )

    assert result.is_error is False
    artifact_file = tmp_path / ".agent" / "artifacts" / "development_result.json"
    stored = json.loads(artifact_file.read_text(encoding="utf-8"))
    assert stored["content"]["status"] == "completed"


def test_handle_submit_artifact_rejects_partial_development_result_without_next_steps(
    tmp_path: Path,
) -> None:
    with pytest.raises(InvalidParamsError, match="next_steps"):
        handle_submit_artifact(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "artifact_type": "development_result",
                "content": _content(
                    {
                        "status": "partial",
                        "summary": "Some work is done.",
                        "files_changed": "- src/example.py",
                    }
                ),
            },
        )
