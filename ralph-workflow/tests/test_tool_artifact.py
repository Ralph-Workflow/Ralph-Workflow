"""Tests for ralph/mcp/tool_artifact.py — MCP artifact submission handlers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.mcp.artifacts.handoffs import (
    delete_markdown_handoff,
    ensure_markdown_handoff_from_artifact,
    render_markdown_handoff,
    sync_markdown_handoff,
)
from ralph.mcp.tools.artifact import (
    ArtifactHandlerDeps,
    _prepare_artifact_submission,
    handle_discard_plan_draft,
    handle_finalize_plan,
    handle_get_plan_draft,
    handle_submit_artifact,
    handle_submit_plan_section,
)
from ralph.mcp.tools.coordination import InvalidParamsError, ToolContent


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
    def __init__(self, failing_path: Path, *, message: str = "artifact store unavailable") -> None:
        super().__init__()
        self._failing_path = failing_path
        self._message = message

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        if path == self._failing_path:
            raise OSError(self._message)
        super().write_text(path, content, encoding=encoding)


def _memory_handler_deps(backend: MemoryBackend) -> ArtifactHandlerDeps:
    return ArtifactHandlerDeps(backend=backend, now_iso=lambda: "2026-04-15T12:00:00+00:00")


@dataclass
class MockSession:
    session_id: str = "test-session"
    drain: str = "development"

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


def test_prepare_artifact_submission_maps_generic_analysis_decision_to_development_drain() -> None:
    payload = {
        "status": "completed",
        "summary": "Implementation looks correct.",
        "what_came_up_short": [],
        "how_to_fix": [],
    }
    artifact_type, parsed_content = _prepare_artifact_submission(
        {
            "artifact_type": "analysis_decision",
            "content": _content(payload),
        },
        session_drain="development_analysis",
    )

    assert artifact_type == "development_analysis_decision"
    assert parsed_content["status"] == "completed"


def test_prepare_artifact_submission_maps_generic_analysis_decision_to_review_drain() -> None:
    payload = {
        "status": "request_changes",
        "summary": "Changes needed.",
        "what_came_up_short": ["Missing tests"],
        "how_to_fix": ["Add unit tests"],
    }
    artifact_type, parsed_content = _prepare_artifact_submission(
        {
            "artifact_type": "analysis_decision",
            "content": _content(payload),
        },
        session_drain="review_analysis",
    )

    assert artifact_type == "review_analysis_decision"
    assert parsed_content["status"] == "request_changes"


def test_handle_submit_artifact_honors_active_policy_analysis_vocabulary(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "pipeline.toml").write_text(
        """
entry_phase = \"planning\"
terminal_phase = \"complete\"

[loop_counters.planning_analysis_iteration]
default_max = 2

[phases.planning]
drain = \"planning\"
role = \"execution\"
[phases.planning.transitions]
on_success = \"planning_analysis\"

[phases.planning_analysis]
drain = \"planning_analysis\"
role = \"analysis\"
prompt_template = \"planning_analysis.jinja\"
[phases.planning_analysis.transitions]
on_success = \"complete\"
on_loopback = \"planning\"
[phases.planning_analysis.loop_policy]
iteration_state_field = \"planning_analysis_iteration\"
[phases.planning_analysis.decisions.accepted]
target = \"complete\"
reset_loop = true
[phases.planning_analysis.decisions.revise]
target = \"planning\"
reset_loop = false

[phases.complete]
drain = \"complete\"
role = \"terminal\"
terminal_outcome = \"success\"
[phases.complete.transitions]
on_success = \"complete\"
on_loopback = \"complete\"

[phases.failed_terminal]
drain = \"complete\"
role = \"terminal\"
terminal_outcome = \"failure\"
[phases.failed_terminal.transitions]
on_success = \"failed_terminal\"
on_loopback = \"failed_terminal\"
""".strip(),
        encoding="utf-8",
    )
    (agent_dir / "artifacts.toml").write_text(
        """
[artifacts.planning_output]
drain = \"planning\"
artifact_type = \"plan\"
prompt_template = \"planning.jinja\"
markdown_summary_path = \".agent/PLAN.md\"

[artifacts.planning_analysis_decision]
drain = \"planning_analysis\"
artifact_type = \"planning_analysis_decision\"
decision_vocabulary = [\"accepted\", \"revise\"]
prompt_template = \"planning_analysis.jinja\"
markdown_summary_path = \".agent/PLANNING_ANALYSIS_DECISION.md\"
""".strip(),
        encoding="utf-8",
    )
    (agent_dir / "agents.toml").write_text(
        """
[agent_chains.planning]
agents = [\"claude\"]

[agent_chains.planning_analysis]
agents = [\"claude\"]

[agent_chains.complete]
agents = [\"claude\"]

[agent_chains.failed_terminal]
agents = [\"claude\"]

[agent_drains.planning]
chain = \"planning\"
drain_class = \"planning\"

[agent_drains.planning_analysis]
chain = \"planning_analysis\"
drain_class = \"analysis\"

[agent_drains.complete]
chain = \"complete\"

[agent_drains.failed_terminal]
chain = \"failed_terminal\"
""".strip(),
        encoding="utf-8",
    )

    result = handle_submit_artifact(
        MockSession(drain="planning_analysis"),
        MockWorkspace(tmp_path),
        {
            "artifact_type": "planning_analysis_decision",
            "content": _content({"status": "accepted", "summary": "Looks good."}),
        },
    )

    assert result.is_error is False


def test_prepare_artifact_submission_rejects_generic_analysis_decision_outside_analysis_drain() -> (
    None
):
    with pytest.raises(InvalidParamsError, match="analysis_decision requires an analysis drain"):
        _prepare_artifact_submission(
            {
                "artifact_type": "analysis_decision",
                "content": _content({"status": "completed"}),
            },
            session_drain="development",
        )


def test_prepare_artifact_submission_rejects_content_path(tmp_path: Path) -> None:
    payload_path = tmp_path / "artifact.json"
    payload_path.write_text("{}", encoding="utf-8")

    with pytest.raises(
        InvalidParamsError,
        match="Use 'content' with a freshly generated JSON string",
    ):
        _prepare_artifact_submission(
            {
                "artifact_type": "plan",
                "content_path": str(payload_path),
            },
            base_path=tmp_path,
        )


def test_prepare_artifact_submission_rejects_missing_content(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError, match="Missing 'content' parameter"):
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
    assert cast("ToolContent", result.content[0]).text == "Artifact submitted: commit_message"
    artifact_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
    assert artifact_file.exists()


def test_legacy_commit_message_payload_points_to_format_doc(tmp_path: Path) -> None:
    with pytest.raises(
        InvalidParamsError, match=r"\.agent/artifact-formats/commit_message\.md"
    ):
        handle_submit_artifact(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "artifact_type": "commit_message",
                "content": _content({"message": "fix: old format"}),
            },
        )
    assert (tmp_path / ".agent" / "artifact-formats" / "commit_message.md").exists()


def test_handle_submit_artifact_rejects_commit_payload_without_subject(tmp_path: Path) -> None:
    with pytest.raises(
        InvalidParamsError, match=r"\.agent/artifact-formats/commit_message\.md"
    ):
        handle_submit_artifact(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "artifact_type": "commit_message",
                "content": _content({"type": "commit", "body": "Missing subject"}),
            },
        )


def test_handle_submit_artifact_rejects_body_and_detailed_fields_together(tmp_path: Path) -> None:
    with pytest.raises(
        InvalidParamsError, match=r"\.agent/artifact-formats/commit_message\.md"
    ):
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


def test_handle_submit_artifact_rejects_commit_payload_with_non_conventional_subject(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        InvalidParamsError, match=r"\.agent/artifact-formats/commit_message\.md"
    ):
        handle_submit_artifact(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "artifact_type": "commit_message",
                "content": _content(
                    {
                        "type": "commit",
                        "subject": "update files",
                    }
                ),
            },
        )


def test_handle_submit_artifact_rejects_non_object_excluded_files_entries(tmp_path: Path) -> None:
    with pytest.raises(
        InvalidParamsError, match=r"\.agent/artifact-formats/commit_message\.md"
    ):
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


def test_handle_submit_artifact_rejects_content_path_for_plan_submission(tmp_path: Path) -> None:
    payload_path = tmp_path / "edited-plan.json"
    payload_path.write_text(_content(_full_plan_payload()), encoding="utf-8")

    with pytest.raises(
        InvalidParamsError,
        match="Artifact submission requires the 'content' field",
    ):
        handle_submit_artifact(
            MockSession(),
            MockWorkspace(tmp_path),
            {
                "artifact_type": "plan",
                "content_path": str(payload_path),
            },
        )


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
    assert (
        (tmp_path / ".agent" / "DEVELOPMENT_RESULT.md")
        .read_text(encoding="utf-8")
        .startswith("# Development Result\n")
    )


def test_handle_submit_artifact_mirrors_issues_to_markdown_handoff(tmp_path: Path) -> None:
    result = handle_submit_artifact(
        MockSession(drain="review"),
        MockWorkspace(tmp_path),
        {
            "artifact_type": "issues",
            "content": _content(
                {
                    "status": "issues_found",
                    "summary": "Review found gaps.",
                    "issues": [
                        {
                            "path": "ralph/pipeline/runner.py",
                            "severity": "high",
                            "summary": "Need better handoff visibility.",
                        }
                    ],
                    "what_came_up_short": ["User cannot see review findings."],
                    "how_to_fix": ["Mirror issues.json to ISSUES.md."],
                }
            ),
        },
    )

    assert result.is_error is False
    issues_md = (tmp_path / ".agent" / "ISSUES.md").read_text(encoding="utf-8")
    assert "# Review Issues" in issues_md
    assert "Need better handoff visibility." in issues_md
    assert "Mirror issues.json to ISSUES.md." in issues_md


def test_handle_submit_artifact_mirrors_fix_result_to_markdown_handoff(tmp_path: Path) -> None:
    result = handle_submit_artifact(
        MockSession(drain="fix"),
        MockWorkspace(tmp_path),
        {
            "artifact_type": "fix_result",
            "content": _content(
                {
                    "summary": "Applied the reviewer fixes.",
                    "files_changed": "- ralph/prompts/materialize.py",
                    "next_steps": "Run review again.",
                }
            ),
        },
    )

    assert result.is_error is False
    fix_md = (tmp_path / ".agent" / "FIX_RESULT.md").read_text(encoding="utf-8")
    assert "# Fix Result" in fix_md
    assert "Applied the reviewer fixes." in fix_md
    assert "Run review again." in fix_md


def test_handle_submit_artifact_mirrors_analysis_decision_to_markdown_handoff(
    tmp_path: Path,
) -> None:
    result = handle_submit_artifact(
        MockSession(drain="development_analysis"),
        MockWorkspace(tmp_path),
        {
            "artifact_type": "analysis_decision",
            "content": _content(
                {
                    "status": "request_changes",
                    "summary": "Implementation needs another pass.",
                    "what_came_up_short": ["The developer cannot see the analysis feedback."],
                    "how_to_fix": ["Mirror the analysis decision into Markdown."],
                }
            ),
        },
    )

    assert result.is_error is False
    decision_md = (tmp_path / ".agent" / "DEVELOPMENT_ANALYSIS_DECISION.md").read_text(
        encoding="utf-8"
    )
    assert "# Development Analysis Decision" in decision_md
    assert "Implementation needs another pass." in decision_md
    assert "Mirror the analysis decision into Markdown." in decision_md


def test_render_markdown_handoff_formats_review_issues_for_agents_and_users() -> None:
    markdown = render_markdown_handoff(
        "issues",
        {
            "status": "issues_found",
            "summary": "Review found gaps.",
            "issues": [
                {
                    "path": "ralph/pipeline/runner.py",
                    "severity": "high",
                    "summary": "Need better handoff visibility.",
                }
            ],
            "what_came_up_short": ["Users cannot see review findings."],
            "how_to_fix": ["Mirror issues.json to ISSUES.md."],
        },
    )

    assert markdown.startswith("# Review Issues\n")
    assert "[high] Need better handoff visibility. (`ralph/pipeline/runner.py`)" in markdown
    assert "## What Came Up Short" in markdown
    assert "## How To Fix" in markdown


def test_sync_and_delete_markdown_handoff_use_shared_contract_with_injected_backend() -> None:
    backend = MemoryBackend()
    workspace_root = Path("/virtual")

    relative_path = sync_markdown_handoff(
        workspace_root,
        "fix_result",
        {
            "summary": "Applied the reviewer fixes.",
            "files_changed": "- ralph/prompts/materialize.py",
            "next_steps": "Run review again.",
        },
        backend=backend,
    )

    assert relative_path == ".agent/FIX_RESULT.md"
    expected = (
        "# Fix Result\n"
        "\n"
        "## Summary\n"
        "\n"
        "Applied the reviewer fixes.\n"
        "\n"
        "## Files Changed\n"
        "\n"
        "- ralph/prompts/materialize.py\n"
        "\n"
        "## Next Steps\n"
        "\n"
        "Run review again.\n"
    )
    assert backend.read_text(Path("/virtual/.agent/FIX_RESULT.md")) == expected

    delete_markdown_handoff(workspace_root, "fix_result", backend=backend)

    assert backend.exists(Path("/virtual/.agent/FIX_RESULT.md")) is False


def test_ensure_markdown_handoff_from_artifact_materializes_analysis_feedback() -> None:
    backend = MemoryBackend()
    workspace_root = Path("/virtual")

    created_path = ensure_markdown_handoff_from_artifact(
        workspace_root,
        "review_analysis_decision",
        json.dumps(
            {
                "type": "review_analysis_decision",
                "content": {
                    "status": "request_changes",
                    "summary": "Fixes are required.",
                    "what_came_up_short": ["The fixer cannot see review-analysis feedback."],
                    "how_to_fix": ["Read the review analysis handoff first."],
                },
            }
        ),
        backend=backend,
    )

    assert created_path == "/virtual/.agent/REVIEW_ANALYSIS_DECISION.md"
    rendered = backend.read_text(Path(created_path))
    assert rendered.startswith("# Review Analysis Decision\n")
    assert "Fixes are required." in rendered
    assert "Read the review analysis handoff first." in rendered


def test_handle_submit_artifact_rolls_back_json_and_markdown_when_handoff_sync_fails() -> None:
    workspace_root = Path("/virtual-failure")
    backend = FailingArtifactBackend(
        workspace_root / ".agent" / "FIX_RESULT.md",
        message="handoff mirror unavailable",
    )

    with pytest.raises(OSError, match="handoff mirror unavailable"):
        handle_submit_artifact(
            MockSession(drain="fix"),
            MockWorkspace(workspace_root),
            {
                "artifact_type": "fix_result",
                "content": _content(
                    {
                        "summary": "Applied the reviewer fixes.",
                        "files_changed": "- ralph/prompts/materialize.py",
                    }
                ),
            },
            deps=_memory_handler_deps(backend),
        )

    assert backend.exists(workspace_root / ".agent/artifacts/fix_result.json") is False
    assert backend.exists(workspace_root / ".agent/FIX_RESULT.md") is False


def test_handle_submit_artifact_rejects_partial_development_result_without_next_steps(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        InvalidParamsError, match=r"\.agent/artifact-formats/development_result\.md"
    ):
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
    assert (tmp_path / ".agent" / "artifact-formats" / "development_result.md").exists()


def test_handle_submit_artifact_invalid_issues_points_to_format_doc(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError, match=r"\.agent/artifact-formats/issues\.md"):
        handle_submit_artifact(
            MockSession(drain="review"),
            MockWorkspace(tmp_path),
            {
                "artifact_type": "issues",
                "content": "[]",
            },
        )
    assert (tmp_path / ".agent" / "artifact-formats" / "issues.md").exists()


def test_handle_submit_artifact_invalid_fix_result_points_to_format_doc(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError, match=r"\.agent/artifact-formats/fix_result\.md"):
        handle_submit_artifact(
            MockSession(drain="fix"),
            MockWorkspace(tmp_path),
            {
                "artifact_type": "fix_result",
                "content": _content({"garbage": "value"}),
            },
        )
    assert (tmp_path / ".agent" / "artifact-formats" / "fix_result.md").exists()
    content = (tmp_path / ".agent" / "artifact-formats" / "fix_result.md").read_text(
        encoding="utf-8"
    )
    assert content.startswith("# fix_result artifact format")


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
    assert (
        tmp_path / ".agent" / "artifact-formats" / "development_analysis_decision.md"
    ).exists()
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
    content = (
        tmp_path / ".agent" / "artifact-formats" / "review_analysis_decision.md"
    ).read_text(encoding="utf-8")
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
    assert (
        tmp_path / ".agent" / "artifact-formats" / "planning_analysis_decision.md"
    ).exists()
    content = (
        tmp_path / ".agent" / "artifact-formats" / "planning_analysis_decision.md"
    ).read_text(encoding="utf-8")
    assert content.startswith("# planning_analysis_decision artifact format")


def test_plan_validation_error_is_not_redirected_through_format_doc(tmp_path: Path) -> None:
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
    assert not (tmp_path / ".agent" / "artifact-formats" / "plan.md").exists()


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
    assert (
        (atomic_path / ".agent" / "PLAN.md")
        .read_text(encoding="utf-8")
        .startswith("# Implementation Plan\n")
    )
    assert (
        (piecewise_path / ".agent" / "PLAN.md")
        .read_text(encoding="utf-8")
        .startswith("# Implementation Plan\n")
    )
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
