"""Tests for ralph/mcp/tool_artifact.py — MCP artifact submission handlers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from ralph.mcp.file_backend import FileBackend
from ralph.mcp.tool_artifact import (
    ArtifactHandlerDeps,
    _prepare_artifact_submission,
    handle_discard_plan_draft,
    handle_finalize_plan,
    handle_get_plan_draft,
    handle_submit_artifact,
    handle_submit_plan_section,
)
from ralph.mcp.tool_coordination import InvalidParamsError


class MemoryBackend(FileBackend):
    def __init__(self) -> None:
        self._files: dict[Path, str] = {}
        self._directories: set[Path] = set()

    def exists(self, path: Path) -> bool:
        return path in self._files or path in self._directories

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del exist_ok
        self._directories.add(path)
        if parents:
            self._directories.update(path.parents)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del encoding
        return self._files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        del encoding
        self._directories.add(path.parent)
        self._directories.update(path.parent.parents)
        self._files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        self._directories.add(destination.parent)
        self._directories.update(destination.parent.parents)
        self._files[destination] = self._files.pop(source)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self._files.pop(path, None)
            return
        del self._files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        if pattern != "*.json":
            return []
        prefix = f"{path}/"
        return [
            candidate
            for candidate in self._files
            if str(candidate).startswith(prefix) and candidate.suffix == ".json"
        ]


class FailingArtifactBackend(MemoryBackend):
    def __init__(self, failing_path: Path) -> None:
        super().__init__()
        self._failing_path = failing_path

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        if path == self._failing_path:
            raise OSError("artifact store unavailable")
        super().write_text(path, content, encoding=encoding)


def _memory_handler_deps(backend: MemoryBackend) -> ArtifactHandlerDeps:
    return ArtifactHandlerDeps(backend=backend, now_iso=lambda: "2026-04-15T12:00:00+00:00")


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


def test_prepare_artifact_submission_reads_content_from_file_path(tmp_path: Path) -> None:
    payload_path = tmp_path / "plan-resubmit.json"
    payload_path.write_text(
        _content(
            {
                "summary": {
                    "context": "Plan MCP rollout from file.",
                    "scope_items": [
                        {"text": "Update validation"},
                        {"text": "Add tests"},
                        {"text": "Update prompts"},
                    ],
                },
                "steps": [{"number": 1, "title": "Validate", "content": "Do the work"}],
                "critical_files": {
                    "primary_files": [{"path": "ralph/mcp/tool_artifact.py", "action": "modify"}]
                },
                "risks_mitigations": [{"risk": "Schema drift", "mitigation": "Add tests"}],
                "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
            }
        ),
        encoding="utf-8",
    )

    artifact_type, parsed_content = _prepare_artifact_submission(
        {
            "artifact_type": "plan",
            "content_path": str(payload_path),
        },
        base_path=tmp_path,
    )

    assert artifact_type == "plan"
    summary = cast("dict[str, object]", parsed_content["summary"])
    assert summary["context"] == "Plan MCP rollout from file."


def test_prepare_artifact_submission_rejects_when_content_and_content_path_are_both_set(
    tmp_path: Path,
) -> None:
    payload_path = tmp_path / "artifact.json"
    payload_path.write_text("{}", encoding="utf-8")

    with pytest.raises(
        InvalidParamsError, match="Provide exactly one of 'content' or 'content_path'"
    ):
        _prepare_artifact_submission(
            {
                "artifact_type": "plan",
                "content": _content(_full_plan_payload()),
                "content_path": str(payload_path),
            },
            base_path=tmp_path,
        )


def test_prepare_artifact_submission_rejects_missing_content_source(tmp_path: Path) -> None:
    with pytest.raises(
        InvalidParamsError, match="Provide exactly one of 'content' or 'content_path'"
    ):
        _prepare_artifact_submission(
            {
                "artifact_type": "plan",
            },
            base_path=tmp_path,
        )


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


def test_handle_submit_artifact_supports_injected_persistence_without_real_filesystem() -> None:
    backend = MemoryBackend()
    workspace = MockWorkspace(Path("/virtual-workspace"))

    result = handle_submit_artifact(
        MockSession(),
        workspace,
        {
            "artifact_type": "commit_message",
            "content": _content({"type": "commit", "subject": "fix: use injected persistence"}),
        },
        deps=_memory_handler_deps(backend),
    )

    assert result.is_error is False
    artifact_payload = json.loads(
        backend.read_text(Path("/virtual-workspace/.agent/artifacts/commit_message.json"))
    )
    assert artifact_payload["content"]["subject"] == "fix: use injected persistence"
    assert (
        backend.read_text(Path("/virtual-workspace/.agent/tmp/commit-message.txt"))
        == "fix: use injected persistence"
    )


def test_handle_submit_artifact_rolls_back_commit_side_effects_when_submit_fails() -> None:
    workspace_root = Path("/virtual-failure")
    backend = FailingArtifactBackend(workspace_root / ".agent/artifacts/commit_message.json")

    with pytest.raises(OSError, match="artifact store unavailable"):
        handle_submit_artifact(
            MockSession(),
            MockWorkspace(workspace_root),
            {
                "artifact_type": "commit_message",
                "content": _content({"type": "commit", "subject": "fix: rollback commit mirror"}),
            },
            deps=_memory_handler_deps(backend),
        )

    assert backend.exists(workspace_root / ".agent/artifacts/commit_message.json") is False
    assert backend.exists(workspace_root / ".agent/tmp/commit_message.json") is False
    assert backend.exists(workspace_root / ".agent/tmp/commit-message.txt") is False


def test_handle_submit_artifact_normalizes_commit_alias_type_to_commit_message(
    tmp_path: Path,
) -> None:
    result = handle_submit_artifact(
        MockSession(),
        MockWorkspace(tmp_path),
        {
            "artifact_type": "commit",
            "content": _content({"type": "commit", "subject": "fix: preserve commit alias"}),
        },
    )

    assert result.is_error is False
    assert result.content[0].text == "Artifact submitted: commit_message"
    artifact_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
    assert artifact_file.exists()


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


def test_handle_submit_artifact_rejects_non_object_excluded_files_entries(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError, match="excluded_files' entries must be objects"):
        handle_submit_artifact(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "artifact_type": "commit_message",
                "content": _content(
                    {
                        "type": "commit",
                        "subject": "fix: validate excluded files",
                        "excluded_files": ["notes/todo.md"],
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


def test_handle_submit_artifact_accepts_content_path_for_plan_resubmission(tmp_path: Path) -> None:
    payload_path = tmp_path / "edited-plan.json"
    payload_path.write_text(_content(_full_plan_payload()), encoding="utf-8")

    result = handle_submit_artifact(
        MockSession(),
        MockWorkspace(tmp_path),
        {
            "artifact_type": "plan",
            "content_path": str(payload_path),
        },
    )

    assert result.is_error is False
    stored = json.loads(
        (tmp_path / ".agent" / "artifacts" / "plan.json").read_text(encoding="utf-8")
    )
    assert stored["content"]["summary"]["context"] == "Plan the MCP validation rollout."


def test_handle_submit_artifact_accepts_existing_artifact_file_for_plan_resubmission(
    tmp_path: Path,
) -> None:
    handle_submit_artifact(
        MockSession(),
        MockWorkspace(tmp_path),
        {
            "artifact_type": "plan",
            "content": _content(_full_plan_payload()),
        },
    )

    artifact_path = tmp_path / ".agent" / "artifacts" / "plan.json"
    persisted = json.loads(artifact_path.read_text(encoding="utf-8"))
    persisted["content"]["summary"]["context"] = "Edited persisted plan payload."
    artifact_path.write_text(json.dumps(persisted), encoding="utf-8")

    result = handle_submit_artifact(
        MockSession(),
        MockWorkspace(tmp_path),
        {
            "artifact_type": "plan",
            "content_path": str(artifact_path),
        },
    )

    assert result.is_error is False
    stored = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert stored["content"]["summary"]["context"] == "Edited persisted plan payload."


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


def _full_plan_payload() -> dict[str, object]:
    return {
        "summary": {
            "context": "Plan the MCP validation rollout.",
            "scope_items": [
                {"text": "Update MCP validation"},
                {"text": "Add tests"},
                {"text": "Update prompts"},
            ],
        },
        "steps": [
            {
                "number": 1,
                "title": "Validate incoming plan payloads",
                "content": "Reject malformed plans before they are written.",
            }
        ],
        "critical_files": {
            "primary_files": [{"path": "ralph/mcp/tool_artifact.py", "action": "modify"}]
        },
        "risks_mitigations": [{"risk": "Schema drift", "mitigation": "Add tests"}],
        "verification_strategy": [
            {"method": "pytest", "expected_outcome": "Plan schema enforced."}
        ],
    }


def _submit_section(
    tmp_path: Path, section: str, payload: object, *, mode: str | None = None
) -> None:
    params: dict[str, object] = {
        "section": section,
        "content": _content(cast("dict[str, object]", payload))
        if isinstance(payload, dict)
        else json.dumps(payload),
    }
    if mode is not None:
        params["mode"] = mode
    handle_submit_plan_section(MockSession(), MockWorkspace(tmp_path), params)


def test_piecewise_plan_submission_produces_same_plan_json_as_atomic(tmp_path: Path) -> None:
    plan = _full_plan_payload()
    atomic_path = tmp_path / "atomic"
    piecewise_path = tmp_path / "piecewise"

    handle_submit_artifact(
        MockSession(),
        MockWorkspace(atomic_path),
        {"artifact_type": "plan", "content": _content(plan)},
    )

    _submit_section(piecewise_path, "summary", plan["summary"])
    _submit_section(piecewise_path, "steps", plan["steps"])
    _submit_section(piecewise_path, "critical_files", plan["critical_files"])
    _submit_section(piecewise_path, "risks_mitigations", plan["risks_mitigations"])
    _submit_section(piecewise_path, "verification_strategy", plan["verification_strategy"])
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
    assert summary["context"] == "Plan the MCP validation rollout."
    # Draft must be gone after a successful finalize.
    assert not (piecewise_path / ".agent" / "artifacts" / ".plan_draft.json").exists()


def test_submit_plan_section_rejects_invalid_section_payload(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError, match=r"\[summary\]"):
        handle_submit_plan_section(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "section": "summary",
                "content": _content(
                    {"context": "too short", "scope_items": [{"text": "only one"}]}
                ),
            },
        )


def test_submit_plan_section_rejects_unknown_section(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError, match="Unknown plan section"):
        handle_submit_plan_section(
            MockSession(),
            MockWorkspace(tmp_path),
            {"section": "bogus", "content": _content({})},
        )


def test_finalize_plan_fails_when_required_section_missing(tmp_path: Path) -> None:
    plan = _full_plan_payload()
    _submit_section(tmp_path, "summary", plan["summary"])
    _submit_section(tmp_path, "steps", plan["steps"])
    _submit_section(tmp_path, "critical_files", plan["critical_files"])
    _submit_section(tmp_path, "risks_mitigations", plan["risks_mitigations"])

    with pytest.raises(InvalidParamsError, match="verification_strategy"):
        handle_finalize_plan(MockSession(), MockWorkspace(tmp_path), {})

    # Draft survives so the agent can fix and retry.
    assert (tmp_path / ".agent" / "artifacts" / ".plan_draft.json").exists()


def test_finalize_plan_fails_when_no_draft(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError, match="No plan draft"):
        handle_finalize_plan(MockSession(), MockWorkspace(tmp_path), {})


def test_submit_plan_section_append_mode_extends_steps_list(tmp_path: Path) -> None:
    plan = _full_plan_payload()
    _submit_section(tmp_path, "summary", plan["summary"])
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

    payload = json.loads(result.content[0].text)
    assert sorted(payload["staged_sections"]) == ["steps", "summary"]


def test_get_plan_draft_when_absent_returns_empty_list(tmp_path: Path) -> None:
    result = handle_get_plan_draft(MockSession(), MockWorkspace(tmp_path), {})
    payload = json.loads(result.content[0].text)
    assert payload == {"staged_sections": []}


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
    draft_payload = json.loads(draft_result.content[0].text)
    assert sorted(draft_payload["staged_sections"]) == [
        "critical_files",
        "risks_mitigations",
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
