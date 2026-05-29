"""Focused tests for prompt materialization artifact-history handling."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.config.enums import AgentTransport
from ralph.mcp.artifacts.history import history_dir_for_artifact, history_index_path
from ralph.mcp.tools.names import SUBMIT_ARTIFACT_TOOL, claude_tool_name
from ralph.policy.models import (
    ArtifactContract,
    ArtifactHistoryPolicy,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
)
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
    resolve_fix_result_content,
    submit_artifact_tool_name_for_transport,
    tool_name_prefix_for_transport,
)
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path


def test_tool_name_prefix_for_claude_interactive() -> None:
    assert tool_name_prefix_for_transport(AgentTransport.CLAUDE_INTERACTIVE) == "mcp__ralph__"


def test_submit_artifact_tool_name_for_transport_returns_claude_namespaced_for_claude() -> None:
    assert submit_artifact_tool_name_for_transport(AgentTransport.CLAUDE) == claude_tool_name(
        SUBMIT_ARTIFACT_TOOL
    )
    assert submit_artifact_tool_name_for_transport(
        AgentTransport.CLAUDE_INTERACTIVE
    ) == claude_tool_name(SUBMIT_ARTIFACT_TOOL)


def test_submit_artifact_tool_name_for_transport_returns_bare_name_for_agy() -> None:
    assert submit_artifact_tool_name_for_transport(AgentTransport.AGY) == SUBMIT_ARTIFACT_TOOL


def test_submit_artifact_tool_name_for_transport_returns_bare_name_for_none() -> None:
    assert submit_artifact_tool_name_for_transport(None) == SUBMIT_ARTIFACT_TOOL


def test_resolve_fix_result_content_reads_fix_result_artifact(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)
    artifact_dir = tmp_path / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    expected = '{"summary": "Applied fixes"}'
    (artifact_dir / "fix_result.json").write_text(expected, encoding="utf-8")

    content, path = resolve_fix_result_content(workspace)
    assert "# Fix Result" in content
    assert "Applied fixes" in content
    assert path == str(tmp_path / ".agent" / "FIX_RESULT.md")


def test_resolve_fix_result_content_returns_placeholder_when_missing(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)

    content, path = resolve_fix_result_content(workspace)
    assert content == "(no fix result available)"
    assert path == ""


def test_fresh_development_prompt_removes_artifact_history_on_fresh_entry(
    tmp_path: Path,
) -> None:
    pipeline_policy = PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                role="execution",
                prompt_template="planning.jinja",
                transitions=PhaseTransition(on_success="development"),
            ),
            "development": PhaseDefinition(
                drain="development",
                role="execution",
                prompt_template="developer_iteration.jinja",
                transitions=PhaseTransition(on_success="complete"),
                artifact_history=ArtifactHistoryPolicy(enabled=True, clear_on_fresh_entry=True),
            ),
            "complete": PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
    )
    artifacts_policy = ArtifactsPolicy(
        artifacts={
            "development_result": ArtifactContract(
                drain="development",
                artifact_type="development_result",
            ),
        }
    )

    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement the feature")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\n1. Do the thing\n")
    history_file = history_index_path(tmp_path / ".agent" / "artifacts", "development_result")
    history_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text("# History\n\n## Entry 1\n", encoding="utf-8")

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development",
            workspace=workspace,
            pipeline_policy=pipeline_policy,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=artifacts_policy,
            previous_phase=None,
        ),
    )

    rendered = workspace.read(prompt_path)
    assert "ARTIFACT HISTORY" not in rendered
    assert str(history_file) not in rendered
    assert history_file.exists() is False


def test_fresh_development_entry_clears_history_when_clear_on_fresh_entry_enabled(
    tmp_path: Path,
) -> None:
    """Fresh development entry clears artifact history when development policy enables it."""
    pipeline_policy = PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                role="execution",
                prompt_template="planning.jinja",
                transitions=PhaseTransition(on_success="development"),
            ),
            "development": PhaseDefinition(
                drain="development",
                role="execution",
                prompt_template="developer_iteration.jinja",
                transitions=PhaseTransition(on_success="complete"),
                artifact_history=ArtifactHistoryPolicy(enabled=True, clear_on_fresh_entry=True),
            ),
            "complete": PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
    )
    artifacts_policy = ArtifactsPolicy(
        artifacts={
            "development_result": ArtifactContract(
                drain="development",
                artifact_type="development_result",
            ),
        }
    )

    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement the feature")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\n1. Do the thing\n")

    # Create history files on disk
    artifact_dir = tmp_path / ".agent" / "artifacts"
    hist_dir = history_dir_for_artifact(artifact_dir, "development_result")
    hist_dir.mkdir(parents=True, exist_ok=True)
    archived_json = hist_dir / "20260506T120000_development_result.json"
    archived_json.write_text('{"type":"development_result"}', encoding="utf-8")
    index_file = history_index_path(artifact_dir, "development_result")
    index_file.write_text("# History", encoding="utf-8")

    materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development",
            workspace=workspace,
            pipeline_policy=pipeline_policy,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=artifacts_policy,
            previous_phase=None,
        ),
    )

    assert not archived_json.exists(), "archive json must be removed on fresh development entry"
    assert not index_file.exists(), "history index must be removed on fresh development entry"
