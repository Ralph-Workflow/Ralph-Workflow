"""Tests for prompt materialization review/fix handoff payloads."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.config.enums import AgentTransport
from ralph.mcp.artifacts.history import history_dir_for_artifact, history_index_path
from ralph.mcp.tools.names import SUBMIT_ARTIFACT_TOOL, claude_tool_name
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


def test_tool_name_prefix_for_claude_interactive() -> None:
    assert tool_name_prefix_for_transport(AgentTransport.CLAUDE_INTERACTIVE) == "mcp__ralph__"


def test_tool_name_prefix_for_opencode_matches_server_namespacing() -> None:
    # OpenCode exposes remote MCP tools as <server>_<tool>; Ralph's server is
    # "ralph". The prompt MUST use this prefix or the model calls a name that does
    # not exist (the ralph_mcp__exec "unavailable tool" incident).
    assert tool_name_prefix_for_transport(AgentTransport.OPENCODE) == "ralph_"


def test_submit_artifact_tool_name_for_opencode_is_server_prefixed() -> None:
    assert (
        submit_artifact_tool_name_for_transport(AgentTransport.OPENCODE)
        == f"ralph_{SUBMIT_ARTIFACT_TOOL}"
    )


def test_tool_name_prefix_for_codex_uses_claude_style_namespacing() -> None:
    # Codex CLI exposes every MCP tool as mcp__<server>__<tool> (same as Claude),
    # so its prompts MUST use that prefix or the model calls a non-existent name.
    assert tool_name_prefix_for_transport(AgentTransport.CODEX) == "mcp__ralph__"


def test_submit_artifact_tool_name_for_codex_is_claude_namespaced() -> None:
    assert submit_artifact_tool_name_for_transport(AgentTransport.CODEX) == claude_tool_name(
        SUBMIT_ARTIFACT_TOOL
    )


def test_submit_artifact_tool_name_for_transport_returns_claude_namespaced_for_claude() -> None:
    assert submit_artifact_tool_name_for_transport(AgentTransport.CLAUDE) == claude_tool_name(
        SUBMIT_ARTIFACT_TOOL
    )
    assert submit_artifact_tool_name_for_transport(
        AgentTransport.CLAUDE_INTERACTIVE
    ) == claude_tool_name(SUBMIT_ARTIFACT_TOOL)


def test_submit_artifact_tool_name_for_transport_returns_bare_name_for_agy() -> None:
    assert submit_artifact_tool_name_for_transport(AgentTransport.AGY) == SUBMIT_ARTIFACT_TOOL


class TestCursorToolNameMatchesClaudeStyle:
    """Pin AC-21: Cursor's MCP tool names use the ``mcp__ralph__`` prefix.

    Cursor's ``agent`` binary follows the documented MCP server-naming
    convention and exposes remote MCP tools as ``mcp__<server>__<tool>``,
    identical to Claude and Codex.  A Cursor prompt that uses a
    different prefix (e.g. ``ralph_``) would cause the model to call a
    non-existent tool name.  The two tests below pin the prefix
    contract:

      * :func:`tool_name_prefix_for_transport` returns
        ``"mcp__ralph__"`` for ``AgentTransport.CURSOR`` (the prefix
        every prompt template uses to construct tool names).
      * :func:`submit_artifact_tool_name_for_transport` returns
        ``claude_tool_name(SUBMIT_ARTIFACT_TOOL)`` (== ``mcp__ralph__submit_artifact``)
        for ``AgentTransport.CURSOR`` (the canonical submit-artifact
        tool name materialized into Cursor prompts).
    """

    def test_tool_name_prefix_for_cursor_is_claude_style(self) -> None:
        assert (
            tool_name_prefix_for_transport(AgentTransport.CURSOR) == "mcp__ralph__"
        )

    def test_submit_artifact_tool_name_for_cursor_is_claude_namespaced(self) -> None:
        assert submit_artifact_tool_name_for_transport(
            AgentTransport.CURSOR
        ) == claude_tool_name(SUBMIT_ARTIFACT_TOOL)

def test_submit_artifact_tool_name_for_transport_returns_bare_name_for_none() -> None:
    assert submit_artifact_tool_name_for_transport(None) == SUBMIT_ARTIFACT_TOOL


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
            "planning_analysis_iteration": LoopCounterConfig(default_max=3),
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
        PromptPhaseContext(
            phase="development",
            workspace=workspace,
            pipeline_policy=pipeline_policy,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=artifacts_policy,
            previous_phase="development_analysis",
        ),
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
    assert "ARTIFACT HISTORY" in rendered
    assert str(index_file) in rendered


def test_fresh_planning_entry_clears_plan_history_preserves_analysis_history(
    tmp_path: Path,
) -> None:
    """Fresh planning entry clears plan history but preserves planning_analysis_decision history."""
    pipeline_policy = PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                role="execution",
                prompt_template="planning.jinja",
                transitions=PhaseTransition(on_success="planning_analysis"),
                artifact_history=ArtifactHistoryPolicy(enabled=True, clear_on_fresh_entry=True),
            ),
            "planning_analysis": PhaseDefinition(
                drain="planning_analysis",
                role="analysis",
                prompt_template="planning_analysis.jinja",
                transitions=PhaseTransition(
                    on_success="development",
                    on_loopback="planning",
                ),
                loop_policy=PhaseLoopPolicy(iteration_state_field="planning_analysis_iteration"),
                decisions={
                    "completed": PhaseDecisionRoute(target="development"),
                    "request_changes": PhaseDecisionRoute(target="planning"),
                },
                artifact_history=ArtifactHistoryPolicy(enabled=True, clear_on_fresh_entry=False),
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
            "plan": ArtifactContract(
                drain="planning",
                artifact_type="plan",
            ),
            "planning_analysis_decision": ArtifactContract(
                drain="planning_analysis",
                artifact_type="planning_analysis_decision",
                decision_vocabulary=["completed", "request_changes"],
            ),
        }
    )

    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement the feature")

    # Create plan history files
    artifact_dir = tmp_path / ".agent" / "artifacts"
    plan_hist_dir = history_dir_for_artifact(artifact_dir, "plan")
    plan_hist_dir.mkdir(parents=True, exist_ok=True)
    plan_archived = plan_hist_dir / "20260506T120000_plan.json"
    plan_archived.write_text('{"type":"plan"}', encoding="utf-8")
    plan_index = history_index_path(artifact_dir, "plan")
    plan_index.write_text("# History", encoding="utf-8")

    # Create planning_analysis_decision history files
    analysis_hist_dir = history_dir_for_artifact(artifact_dir, "planning_analysis_decision")
    analysis_hist_dir.mkdir(parents=True, exist_ok=True)
    analysis_archived = analysis_hist_dir / "20260506T120000_planning_analysis_decision.json"
    analysis_archived.write_text('{"type":"planning_analysis_decision"}', encoding="utf-8")
    analysis_index = history_index_path(artifact_dir, "planning_analysis_decision")
    analysis_index.write_text("# History", encoding="utf-8")

    materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="planning",
            workspace=workspace,
            pipeline_policy=pipeline_policy,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=artifacts_policy,
            previous_phase=None,
        ),
    )

    # Plan history is cleared (clear_on_fresh_entry=true for planning)
    assert not plan_archived.exists(), "plan archive must be removed on fresh planning entry"
    assert not plan_index.exists(), "plan history index must be removed on fresh planning entry"
    # planning_analysis_decision history is preserved (clear_on_fresh_entry=false)
    assert analysis_archived.exists(), "planning_analysis_decision archive must be preserved"
    assert analysis_index.exists(), "planning_analysis_decision history index must be preserved"


def test_planning_analysis_to_development_clears_history_per_policy(
    tmp_path: Path,
) -> None:
    """planning_analysis→development clears planning and development history per policy."""
    pipeline_policy = PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                role="execution",
                prompt_template="planning.jinja",
                transitions=PhaseTransition(on_success="planning_analysis"),
                artifact_history=ArtifactHistoryPolicy(enabled=True, clear_on_fresh_entry=True),
            ),
            "planning_analysis": PhaseDefinition(
                drain="planning_analysis",
                role="analysis",
                prompt_template="planning_analysis.jinja",
                transitions=PhaseTransition(
                    on_success="development",
                    on_loopback="planning",
                ),
                loop_policy=PhaseLoopPolicy(iteration_state_field="planning_analysis_iteration"),
                decisions={
                    "completed": PhaseDecisionRoute(target="development"),
                    "request_changes": PhaseDecisionRoute(target="planning"),
                },
                artifact_history=ArtifactHistoryPolicy(enabled=True, clear_on_fresh_entry=False),
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
            "plan": ArtifactContract(
                drain="planning",
                artifact_type="plan",
            ),
            "planning_analysis_decision": ArtifactContract(
                drain="planning_analysis",
                artifact_type="planning_analysis_decision",
                decision_vocabulary=["completed", "request_changes"],
            ),
            "development_result": ArtifactContract(
                drain="development",
                artifact_type="development_result",
            ),
        }
    )

    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement the feature")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\n1. Do the thing\n")
    # Create plan history files
    artifact_dir = tmp_path / ".agent" / "artifacts"
    plan_hist_dir = history_dir_for_artifact(artifact_dir, "plan")
    plan_hist_dir.mkdir(parents=True, exist_ok=True)
    plan_archived = plan_hist_dir / "20260506T120000_plan.json"
    plan_archived.write_text('{"type":"plan"}', encoding="utf-8")
    plan_index = history_index_path(artifact_dir, "plan")
    plan_index.write_text("# History", encoding="utf-8")

    # Create planning_analysis_decision history files
    analysis_hist_dir = history_dir_for_artifact(artifact_dir, "planning_analysis_decision")
    analysis_hist_dir.mkdir(parents=True, exist_ok=True)
    analysis_archived = analysis_hist_dir / "20260506T120000_planning_analysis_decision.json"
    analysis_archived.write_text('{"type":"planning_analysis_decision"}', encoding="utf-8")
    analysis_index = history_index_path(artifact_dir, "planning_analysis_decision")
    analysis_index.write_text("# History", encoding="utf-8")

    # Create development_result history files
    dev_hist_dir = history_dir_for_artifact(artifact_dir, "development_result")
    dev_hist_dir.mkdir(parents=True, exist_ok=True)
    dev_archived = dev_hist_dir / "20260506T120000_development_result.json"
    dev_archived.write_text('{"type":"development_result"}', encoding="utf-8")
    dev_index = history_index_path(artifact_dir, "development_result")
    dev_index.write_text("# History", encoding="utf-8")

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
            previous_phase="planning_analysis",
        ),
    )

    # Plan history is cleared (clear_on_fresh_entry=true for planning)
    assert not plan_archived.exists(), "plan archive must be removed"
    assert not plan_index.exists(), "plan history index must be removed"
    # planning_analysis_decision history is preserved (clear_on_fresh_entry=false)
    assert analysis_archived.exists(), "planning_analysis_decision archive must be preserved"
    assert analysis_index.exists(), "planning_analysis_decision history index must be preserved"
    # development_result history is cleared (clear_on_fresh_entry=true for development)
    assert not dev_archived.exists(), "development_result archive must be removed"
    assert not dev_index.exists(), "development_result history index must be removed"


def test_development_analysis_to_development_commit_clears_history_per_policy(
    tmp_path: Path,
) -> None:
    """development_analysis→development_commit clears development history per policy."""
    pipeline_policy = PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                role="execution",
                prompt_template="planning.jinja",
                transitions=PhaseTransition(on_success="planning_analysis"),
                artifact_history=ArtifactHistoryPolicy(enabled=True, clear_on_fresh_entry=True),
            ),
            "planning_analysis": PhaseDefinition(
                drain="planning_analysis",
                role="analysis",
                prompt_template="planning_analysis.jinja",
                transitions=PhaseTransition(
                    on_success="development",
                    on_loopback="planning",
                ),
                loop_policy=PhaseLoopPolicy(iteration_state_field="planning_analysis_iteration"),
                decisions={
                    "completed": PhaseDecisionRoute(target="development"),
                },
                artifact_history=ArtifactHistoryPolicy(enabled=True, clear_on_fresh_entry=False),
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
                    on_success="development_commit",
                    on_loopback="development",
                ),
                loop_policy=PhaseLoopPolicy(iteration_state_field="development_analysis_iteration"),
                decisions={
                    "completed": PhaseDecisionRoute(target="development_commit"),
                    "request_changes": PhaseDecisionRoute(target="development"),
                },
                artifact_history=ArtifactHistoryPolicy(enabled=True, clear_on_fresh_entry=False),
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                role="commit",
                prompt_template="commit_message.jinja",
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
        loop_counters={
            "planning_analysis_iteration": LoopCounterConfig(default_max=3),
            "development_analysis_iteration": LoopCounterConfig(default_max=3),
        },
    )

    artifacts_policy = ArtifactsPolicy(
        artifacts={
            "plan": ArtifactContract(
                drain="planning",
                artifact_type="plan",
            ),
            "development_result": ArtifactContract(
                drain="development",
                artifact_type="development_result",
            ),
            "development_analysis_decision": ArtifactContract(
                drain="development_analysis",
                artifact_type="development_analysis_decision",
                decision_vocabulary=["completed", "request_changes"],
            ),
        }
    )

    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement the feature")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\n1. Do the thing\n")

    # Create plan history files
    artifact_dir = tmp_path / ".agent" / "artifacts"
    plan_hist_dir = history_dir_for_artifact(artifact_dir, "plan")
    plan_hist_dir.mkdir(parents=True, exist_ok=True)
    plan_archived = plan_hist_dir / "20260506T120000_plan.json"
    plan_archived.write_text('{"type":"plan"}', encoding="utf-8")
    plan_index = history_index_path(artifact_dir, "plan")
    plan_index.write_text("# History", encoding="utf-8")

    # Create development_result history files
    dev_hist_dir = history_dir_for_artifact(artifact_dir, "development_result")
    dev_hist_dir.mkdir(parents=True, exist_ok=True)
    dev_archived = dev_hist_dir / "20260506T120000_development_result.json"
    dev_archived.write_text('{"type":"development_result"}', encoding="utf-8")
    dev_index = history_index_path(artifact_dir, "development_result")
    dev_index.write_text("# History", encoding="utf-8")
    # Create development_analysis_decision history files
    analysis_hist_dir = history_dir_for_artifact(artifact_dir, "development_analysis_decision")
    analysis_hist_dir.mkdir(parents=True, exist_ok=True)
    analysis_archived = analysis_hist_dir / "20260506T120000_development_analysis_decision.json"
    analysis_archived.write_text('{"type":"development_analysis_decision"}', encoding="utf-8")
    analysis_index = history_index_path(artifact_dir, "development_analysis_decision")
    analysis_index.write_text("# History", encoding="utf-8")

    materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development_commit",
            workspace=workspace,
            pipeline_policy=pipeline_policy,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=artifacts_policy,
            previous_phase="development_analysis",
        ),
    )

    # Plan history is cleared (clear_on_fresh_entry=true for planning)
    assert not plan_archived.exists(), "plan archive must be removed"
    assert not plan_index.exists(), "plan history index must be removed"
    # development_analysis_decision history is preserved (clear_on_fresh_entry=false)
    assert analysis_archived.exists(), "development_analysis_decision archive must be preserved"
    assert analysis_index.exists(), "development_analysis_decision history index must be preserved"
    # development_result history is cleared (clear_on_fresh_entry=true for development)
    assert not dev_archived.exists(), "development_result archive must be removed"
    assert not dev_index.exists(), "development_result history index must be removed"


def test_planning_analysis_bypass_clears_history_per_policy(
    tmp_path: Path,
) -> None:
    """planning bypass: iteration cap hit routes planning->development (skipping analysis)."""
    pipeline_policy = PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                role="execution",
                prompt_template="planning.jinja",
                transitions=PhaseTransition(on_success="planning_analysis"),
                artifact_history=ArtifactHistoryPolicy(enabled=True, clear_on_fresh_entry=True),
            ),
            "planning_analysis": PhaseDefinition(
                drain="planning_analysis",
                role="analysis",
                prompt_template="planning_analysis.jinja",
                transitions=PhaseTransition(
                    on_success="development",
                    on_loopback="planning",
                ),
                loop_policy=PhaseLoopPolicy(iteration_state_field="planning_analysis_iteration"),
                decisions={
                    "completed": PhaseDecisionRoute(target="development"),
                    "request_changes": PhaseDecisionRoute(target="planning"),
                },
                artifact_history=ArtifactHistoryPolicy(enabled=True, clear_on_fresh_entry=False),
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
            "plan": ArtifactContract(
                drain="planning",
                artifact_type="plan",
            ),
            "planning_analysis_decision": ArtifactContract(
                drain="planning_analysis",
                artifact_type="planning_analysis_decision",
                decision_vocabulary=["completed", "request_changes"],
            ),
            "development_result": ArtifactContract(
                drain="development",
                artifact_type="development_result",
            ),
        }
    )

    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement the feature")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\n1. Do the thing\n")

    # Create plan history files
    artifact_dir = tmp_path / ".agent" / "artifacts"
    plan_hist_dir = history_dir_for_artifact(artifact_dir, "plan")
    plan_hist_dir.mkdir(parents=True, exist_ok=True)
    plan_archived = plan_hist_dir / "20260506T120000_plan.json"
    plan_archived.write_text('{"type":"plan"}', encoding="utf-8")
    plan_index = history_index_path(artifact_dir, "plan")
    plan_index.write_text("# History", encoding="utf-8")

    # Create planning_analysis_decision history files
    analysis_hist_dir = history_dir_for_artifact(artifact_dir, "planning_analysis_decision")
    analysis_hist_dir.mkdir(parents=True, exist_ok=True)
    analysis_archived = analysis_hist_dir / "20260506T120000_planning_analysis_decision.json"
    analysis_archived.write_text('{"type":"planning_analysis_decision"}', encoding="utf-8")
    analysis_index = history_index_path(artifact_dir, "planning_analysis_decision")
    analysis_index.write_text("# History", encoding="utf-8")

    # Create development_result history files
    dev_hist_dir = history_dir_for_artifact(artifact_dir, "development_result")
    dev_hist_dir.mkdir(parents=True, exist_ok=True)
    dev_archived = dev_hist_dir / "20260506T120000_development_result.json"
    dev_archived.write_text('{"type":"development_result"}', encoding="utf-8")
    dev_index = history_index_path(artifact_dir, "development_result")
    dev_index.write_text("# History", encoding="utf-8")

    # Bypass: previous_phase is "planning" (execution role), NOT "planning_analysis"
    # The chain is: planning -> planning_analysis -> development
    # But we're bypassing planning_analysis, so: planning -> development
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
            previous_phase="planning",  # Bypass: skipped planning_analysis
        ),
    )

    # Plan history is cleared (clear_on_fresh_entry=true for planning)
    assert not plan_archived.exists(), "plan archive must be removed on bypass"
    assert not plan_index.exists(), "plan history index must be removed on bypass"
    # planning_analysis_decision history is preserved (clear_on_fresh_entry=false)
    assert analysis_archived.exists(), "planning_analysis_decision archive must be preserved"
    assert analysis_index.exists(), "planning_analysis_decision history index must be preserved"
    # development_result history is cleared (clear_on_fresh_entry=true for development)
    assert not dev_archived.exists(), "development_result archive must be removed on bypass"
    assert not dev_index.exists(), "development_result history index must be removed on bypass"


def test_development_analysis_bypass_clears_history_per_policy(
    tmp_path: Path,
) -> None:
    """development bypass: iteration cap hit routes development->development_commit."""
    pipeline_policy = PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                role="execution",
                prompt_template="planning.jinja",
                transitions=PhaseTransition(on_success="planning_analysis"),
                artifact_history=ArtifactHistoryPolicy(enabled=True, clear_on_fresh_entry=True),
            ),
            "planning_analysis": PhaseDefinition(
                drain="planning_analysis",
                role="analysis",
                prompt_template="planning_analysis.jinja",
                transitions=PhaseTransition(
                    on_success="development",
                    on_loopback="planning",
                ),
                loop_policy=PhaseLoopPolicy(iteration_state_field="planning_analysis_iteration"),
                decisions={
                    "completed": PhaseDecisionRoute(target="development"),
                },
                artifact_history=ArtifactHistoryPolicy(enabled=True, clear_on_fresh_entry=False),
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
                    on_success="development_commit",
                    on_loopback="development",
                ),
                loop_policy=PhaseLoopPolicy(iteration_state_field="development_analysis_iteration"),
                decisions={
                    "completed": PhaseDecisionRoute(target="development_commit"),
                    "request_changes": PhaseDecisionRoute(target="development"),
                },
                artifact_history=ArtifactHistoryPolicy(enabled=True, clear_on_fresh_entry=False),
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                role="commit",
                prompt_template="commit_message.jinja",
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
        loop_counters={
            "planning_analysis_iteration": LoopCounterConfig(default_max=3),
            "development_analysis_iteration": LoopCounterConfig(default_max=3),
        },
    )

    artifacts_policy = ArtifactsPolicy(
        artifacts={
            "plan": ArtifactContract(
                drain="planning",
                artifact_type="plan",
            ),
            "development_result": ArtifactContract(
                drain="development",
                artifact_type="development_result",
            ),
            "development_analysis_decision": ArtifactContract(
                drain="development_analysis",
                artifact_type="development_analysis_decision",
                decision_vocabulary=["completed", "request_changes"],
            ),
        }
    )

    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement the feature")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\n1. Do the thing\n")

    # Create plan history files
    artifact_dir = tmp_path / ".agent" / "artifacts"
    plan_hist_dir = history_dir_for_artifact(artifact_dir, "plan")
    plan_hist_dir.mkdir(parents=True, exist_ok=True)
    plan_archived = plan_hist_dir / "20260506T120000_plan.json"
    plan_archived.write_text('{"type":"plan"}', encoding="utf-8")
    plan_index = history_index_path(artifact_dir, "plan")
    plan_index.write_text("# History", encoding="utf-8")

    # Create development_result history files
    dev_hist_dir = history_dir_for_artifact(artifact_dir, "development_result")
    dev_hist_dir.mkdir(parents=True, exist_ok=True)
    dev_archived = dev_hist_dir / "20260506T120000_development_result.json"
    dev_archived.write_text('{"type":"development_result"}', encoding="utf-8")
    dev_index = history_index_path(artifact_dir, "development_result")
    dev_index.write_text("# History", encoding="utf-8")

    # Create development_analysis_decision history files
    analysis_hist_dir = history_dir_for_artifact(artifact_dir, "development_analysis_decision")
    analysis_hist_dir.mkdir(parents=True, exist_ok=True)
    analysis_archived = analysis_hist_dir / "20260506T120000_development_analysis_decision.json"
    analysis_archived.write_text('{"type":"development_analysis_decision"}', encoding="utf-8")
    analysis_index = history_index_path(artifact_dir, "development_analysis_decision")
    analysis_index.write_text("# History", encoding="utf-8")

    # Bypass: previous_phase is "development" (execution role), NOT "development_analysis"
    # The chain is: development -> development_analysis -> development_commit
    # But we're bypassing development_analysis, so: development -> development_commit
    materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development_commit",
            workspace=workspace,
            pipeline_policy=pipeline_policy,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=artifacts_policy,
            previous_phase="development",  # Bypass: skipped development_analysis
        ),
    )

    # Plan history is cleared (clear_on_fresh_entry=true for planning)
    assert not plan_archived.exists(), "plan archive must be removed on bypass"
    assert not plan_index.exists(), "plan history index must be removed on bypass"
    # development_analysis_decision history is preserved (clear_on_fresh_entry=false)
    assert analysis_archived.exists(), "development_analysis_decision archive must be preserved"
    assert analysis_index.exists(), "development_analysis_decision history index must be preserved"
    # development_result history is cleared (clear_on_fresh_entry=true for development)
    assert not dev_archived.exists(), "development_result archive must be removed on bypass"
    assert not dev_index.exists(), "development_result history index must be removed on bypass"


def test_commit_cleanup_phase_renders_prompt_successfully(tmp_path: Path) -> None:
    p = PipelinePolicy(
        phases={
            "development_commit_cleanup": PhaseDefinition(
                drain="commit",
                role="commit_cleanup",
                prompt_template="commit_cleanup.jinja",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="development_commit_cleanup",
                    on_failure="failed_terminal",
                ),
                loop_policy=PhaseLoopPolicy(iteration_state_field="commit_cleanup_iteration"),
            ),
            "complete": PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="complete"),
            ),
        },
        entry_phase="development_commit_cleanup",
        terminal_phase="complete",
        loop_counters={"commit_cleanup_iteration": LoopCounterConfig(default_max=3)},
    )
    ws = MemoryWorkspace(root=str(tmp_path))
    ws.write("PROMPT.md", "x")
    ws.write(".agent/PLAN.md", "# Plan")
    caps = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    ctx = PromptPhaseContext(
        phase="development_commit_cleanup",
        workspace=ws,
        pipeline_policy=p,
        session_caps=caps,
        workspace_root=tmp_path,
    )
    rendered = ws.read(
        materialize_prompt_for_phase(
            ctx,
            PromptPhaseOptions(artifacts_policy=ArtifactsPolicy(artifacts={}), previous_phase=None),
        )
    )
    assert "ralph_submit_artifact" in rendered and "DIFF" in rendered
