"""Tests for prompt materialization review/fix handoff payloads."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.config.enums import AgentTransport
from ralph.mcp.artifacts.history import history_dir_for_artifact, history_index_path
from ralph.policy.models import (
    ArtifactContract,
    ArtifactHistoryPolicy,
    ArtifactsPolicy,
    LoopCounterConfig,
    PhaseDecisionRoute,
    PhaseDefinition,
    PhaseLoopPolicy,
    PhaseTransition,
    PipelinePolicy,
)
from ralph.prompts.materialize import (
    _resolve_fix_result_content,
    materialize_prompt_for_phase,
    tool_name_prefix_for_transport,
)
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path


def test_resolve_fix_result_content_reads_fix_result_artifact(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)
    artifact_dir = tmp_path / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    expected = '{"summary": "Applied fixes"}'
    (artifact_dir / "fix_result.json").write_text(expected, encoding="utf-8")

    content, path = _resolve_fix_result_content(workspace)
    assert "# Fix Result" in content
    assert "Applied fixes" in content
    assert path == str(tmp_path / ".agent" / "FIX_RESULT.md")


def test_resolve_fix_result_content_returns_placeholder_when_missing(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)

    content, path = _resolve_fix_result_content(workspace)
    assert content == "(no fix result available)"
    assert path == ""


def test_tool_name_prefix_for_claude_interactive() -> None:
    assert tool_name_prefix_for_transport(AgentTransport.CLAUDE_INTERACTIVE) == "mcp__ralph__"


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
        phase="development",
        workspace=workspace,
        pipeline_policy=pipeline_policy,
        artifacts_policy=artifacts_policy,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
        previous_phase=None,
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
        phase="development",
        workspace=workspace,
        pipeline_policy=pipeline_policy,
        artifacts_policy=artifacts_policy,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
        previous_phase=None,
    )

    assert not archived_json.exists(), "archive json must be removed on fresh development entry"
    assert not index_file.exists(), "history index must be removed on fresh development entry"


def test_development_analysis_loopback_preserves_development_artifact_history(
    tmp_path: Path,
) -> None:
    """Development loopback from development_analysis must not clear artifact history."""
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
                transitions=PhaseTransition(on_success="development_analysis"),
                artifact_history=ArtifactHistoryPolicy(enabled=True, clear_on_fresh_entry=True),
            ),
            "development_analysis": PhaseDefinition(
                drain="development_analysis",
                role="analysis",
                prompt_template="development_analysis.jinja",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="development",
                ),
                loop_policy=PhaseLoopPolicy(iteration_state_field="development_analysis_iteration"),
                decisions={
                    "approve": PhaseDecisionRoute(target="complete"),
                    "request_changes": PhaseDecisionRoute(target="development"),
                },
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
        loop_counters={
            "development_analysis_iteration": LoopCounterConfig(default_max=3),
        },
    )

    artifacts_policy = ArtifactsPolicy(
        artifacts={
            "development_result": ArtifactContract(
                drain="development",
                artifact_type="development_result",
            ),
            "development_analysis_decision": ArtifactContract(
                drain="development_analysis",
                artifact_type="development_analysis_decision",
                decision_vocabulary=["approve", "request_changes"],
            ),
        }
    )

    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement the feature")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\n1. Do the thing\n")
    workspace.write(
        ".agent/artifacts/development_analysis_decision.json",
        json.dumps(
            {
                "type": "development_analysis_decision",
                "content": {
                    "status": "request_changes",
                    "summary": "Need more tests.",
                    "what_came_up_short": ["Missing coverage."],
                    "how_to_fix": ["Add tests."],
                },
            }
        ),
    )

    # Create history files on disk
    artifact_dir = tmp_path / ".agent" / "artifacts"
    hist_dir = history_dir_for_artifact(artifact_dir, "development_result")
    hist_dir.mkdir(parents=True, exist_ok=True)
    archived_json = hist_dir / "20260506T120000_development_result.json"
    archived_json.write_text('{"type":"development_result"}', encoding="utf-8")
    index_file = history_index_path(artifact_dir, "development_result")
    index_file.write_text("# History", encoding="utf-8")

    materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=pipeline_policy,
        artifacts_policy=artifacts_policy,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
        previous_phase="development_analysis",
    )

    assert archived_json.exists(), "archive json must be preserved on development loopback"
    assert index_file.exists(), "history index must be preserved on development loopback"


def test_development_prompt_includes_artifact_history_path_when_history_exists(
    tmp_path: Path,
) -> None:
    """Development prompt references artifact history index when it exists on disk."""
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
                artifact_history=ArtifactHistoryPolicy(enabled=True, clear_on_fresh_entry=False),
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

    # Create the history index file on disk
    artifact_dir = tmp_path / ".agent" / "artifacts"
    index_file = history_index_path(artifact_dir, "development_result")
    index_file.parent.mkdir(parents=True, exist_ok=True)
    index_file.write_text("# History\n\n## Entry 1\n", encoding="utf-8")

    prompt_path = materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=pipeline_policy,
        artifacts_policy=artifacts_policy,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
        previous_phase=None,
    )

    rendered = workspace.read(prompt_path)
    assert "ARTIFACT HISTORY" in rendered
    assert str(index_file) in rendered
