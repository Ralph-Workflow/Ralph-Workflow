from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.config.enums import AgentTransport
from ralph.mcp.artifacts.history import history_dir_for_artifact, history_index_path
from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.mcp.tools.names import SUBMIT_MD_ARTIFACT_TOOL, claude_tool_name
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
from ralph.prompts.types import SessionCapabilities
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch

FIX_RESULT_DOC = """---
type: fix_result
---
## Summary
- [S1] Applied fixes
## Files Changed
- [F1] ralph/example.py
"""


def test_resolve_fix_result_content_reads_markdown_handoff(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)
    handoff = tmp_path / ".agent" / "FIX_RESULT.md"
    handoff.parent.mkdir(parents=True, exist_ok=True)
    handoff.write_text(FIX_RESULT_DOC, encoding="utf-8")

    content, path = resolve_fix_result_content(workspace)
    assert content == FIX_RESULT_DOC
    assert path == str(tmp_path / ".agent" / "FIX_RESULT.md")


def test_resolve_fix_result_content_falls_back_to_markdown_artifact(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)
    artifact_dir = tmp_path / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "fix_result.md").write_text(FIX_RESULT_DOC, encoding="utf-8")

    content, path = resolve_fix_result_content(workspace)
    assert content == FIX_RESULT_DOC
    assert path == str(artifact_dir / "fix_result.md")


def test_resolve_fix_result_content_returns_placeholder_when_missing(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)

    content, path = resolve_fix_result_content(workspace)
    assert content == "(no fix result available)"
    assert path == ""


def test_tool_name_prefix_for_claude_interactive() -> None:
    assert tool_name_prefix_for_transport(AgentTransport.CLAUDE_INTERACTIVE) == "mcp__ralph__"


def test_tool_name_prefix_for_opencode_matches_server_namespacing() -> None:
    assert tool_name_prefix_for_transport(AgentTransport.OPENCODE) == "ralph_"


def test_submit_artifact_tool_name_for_opencode_is_server_prefixed() -> None:
    assert (
        submit_artifact_tool_name_for_transport(AgentTransport.OPENCODE)
        == f"ralph_{SUBMIT_MD_ARTIFACT_TOOL}"
    )


def test_tool_name_prefix_for_codex_uses_claude_style_namespacing() -> None:
    assert tool_name_prefix_for_transport(AgentTransport.CODEX) == "mcp__ralph__"


def test_submit_artifact_tool_name_for_codex_is_claude_namespaced() -> None:
    assert submit_artifact_tool_name_for_transport(AgentTransport.CODEX) == claude_tool_name(
        SUBMIT_MD_ARTIFACT_TOOL
    )


def test_submit_artifact_tool_name_for_transport_returns_claude_namespaced_for_claude() -> None:
    assert submit_artifact_tool_name_for_transport(AgentTransport.CLAUDE) == claude_tool_name(
        SUBMIT_MD_ARTIFACT_TOOL
    )
    assert submit_artifact_tool_name_for_transport(
        AgentTransport.CLAUDE_INTERACTIVE
    ) == claude_tool_name(SUBMIT_MD_ARTIFACT_TOOL)


def test_submit_artifact_tool_name_for_transport_returns_bare_name_for_agy() -> None:
    assert submit_artifact_tool_name_for_transport(AgentTransport.AGY) == SUBMIT_MD_ARTIFACT_TOOL


class TestCursorToolNameMatchesClaudeStyle:
    def test_tool_name_prefix_for_cursor_is_claude_style(self) -> None:
        assert tool_name_prefix_for_transport(AgentTransport.CURSOR) == "mcp__ralph__"

    def test_submit_artifact_tool_name_for_cursor_is_claude_namespaced(self) -> None:
        assert submit_artifact_tool_name_for_transport(AgentTransport.CURSOR) == claude_tool_name(
            SUBMIT_MD_ARTIFACT_TOOL
        )


def test_submit_artifact_tool_name_for_transport_returns_bare_name_for_none() -> None:
    assert submit_artifact_tool_name_for_transport(None) == SUBMIT_MD_ARTIFACT_TOOL


def test_development_analysis_loopback_preserves_development_artifact_history(
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
        ".agent/artifacts/development_analysis_decision.md",
        "---\ntype: development_analysis_decision\nstatus: request_changes\n---\n"
        "## Summary\n- [S1] Need more tests: coverage is missing, add tests.\n"
        "## What Came Up Short\n- [DA-1] The changed path lacks coverage.\n"
        "## How To Fix\n- [DA-1] Add and run focused regression coverage.\n",
    )
    artifact_dir = tmp_path / ".agent" / "artifacts"
    hist_dir = history_dir_for_artifact(artifact_dir, "development_result")
    hist_dir.mkdir(parents=True, exist_ok=True)
    archived_md = hist_dir / "20260506T120000_development_result.md"
    archived_md.write_text("---\ntype: development_result\n---\n", encoding="utf-8")
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

    assert archived_md.exists(), "archive must be preserved on development loopback"
    assert index_file.exists(), "history index must be preserved on development loopback"


def test_development_prompt_includes_artifact_history_path_when_history_exists(
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

    artifact_dir = tmp_path / ".agent" / "artifacts"
    plan_hist_dir = history_dir_for_artifact(artifact_dir, "plan")
    plan_hist_dir.mkdir(parents=True, exist_ok=True)
    plan_archived = plan_hist_dir / "20260506T120000_plan.md"
    plan_archived.write_text("---\ntype: plan\n---\n", encoding="utf-8")
    plan_index = history_index_path(artifact_dir, "plan")
    plan_index.write_text("# History", encoding="utf-8")

    analysis_hist_dir = history_dir_for_artifact(artifact_dir, "planning_analysis_decision")
    analysis_hist_dir.mkdir(parents=True, exist_ok=True)
    analysis_archived = analysis_hist_dir / "20260506T120000_planning_analysis_decision.md"
    analysis_archived.write_text("---\ntype: planning_analysis_decision\n---\n", encoding="utf-8")
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

    assert not plan_archived.exists(), "plan archive must be removed on fresh planning entry"
    assert not plan_index.exists(), "plan history index must be removed on fresh planning entry"
    assert analysis_archived.exists(), "planning_analysis_decision archive must be preserved"
    assert analysis_index.exists(), "planning_analysis_decision history index must be preserved"


def test_planning_analysis_to_development_clears_history_per_policy(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:

    def _mock_docs(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(
        "ralph.prompts.materialize.SkillManager.get_docs_mcp_available",
        _mock_docs,
    )
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
    artifact_dir = tmp_path / ".agent" / "artifacts"
    plan_hist_dir = history_dir_for_artifact(artifact_dir, "plan")
    plan_hist_dir.mkdir(parents=True, exist_ok=True)
    plan_archived = plan_hist_dir / "20260506T120000_plan.md"
    plan_archived.write_text("---\ntype: plan\n---\n", encoding="utf-8")
    plan_index = history_index_path(artifact_dir, "plan")
    plan_index.write_text("# History", encoding="utf-8")

    analysis_hist_dir = history_dir_for_artifact(artifact_dir, "planning_analysis_decision")
    analysis_hist_dir.mkdir(parents=True, exist_ok=True)
    analysis_archived = analysis_hist_dir / "20260506T120000_planning_analysis_decision.md"
    analysis_archived.write_text("---\ntype: planning_analysis_decision\n---\n", encoding="utf-8")
    analysis_index = history_index_path(artifact_dir, "planning_analysis_decision")
    analysis_index.write_text("# History", encoding="utf-8")

    dev_hist_dir = history_dir_for_artifact(artifact_dir, "development_result")
    dev_hist_dir.mkdir(parents=True, exist_ok=True)
    dev_archived = dev_hist_dir / "20260506T120000_development_result.md"
    dev_archived.write_text("---\ntype: development_result\n---\n", encoding="utf-8")
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

    assert not plan_archived.exists(), "plan archive must be removed"
    assert not plan_index.exists(), "plan history index must be removed"
    assert analysis_archived.exists(), "planning_analysis_decision archive must be preserved"
    assert analysis_index.exists(), "planning_analysis_decision history index must be preserved"
    assert not dev_archived.exists(), "development_result archive must be removed"
    assert not dev_index.exists(), "development_result history index must be removed"


def test_development_analysis_to_development_commit_clears_history_per_policy(
    tmp_path: Path,
) -> None:
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

    artifact_dir = tmp_path / ".agent" / "artifacts"
    plan_hist_dir = history_dir_for_artifact(artifact_dir, "plan")
    plan_hist_dir.mkdir(parents=True, exist_ok=True)
    plan_archived = plan_hist_dir / "20260506T120000_plan.md"
    plan_archived.write_text("---\ntype: plan\n---\n", encoding="utf-8")
    plan_index = history_index_path(artifact_dir, "plan")
    plan_index.write_text("# History", encoding="utf-8")

    dev_hist_dir = history_dir_for_artifact(artifact_dir, "development_result")
    dev_hist_dir.mkdir(parents=True, exist_ok=True)
    dev_archived = dev_hist_dir / "20260506T120000_development_result.md"
    dev_archived.write_text("---\ntype: development_result\n---\n", encoding="utf-8")
    dev_index = history_index_path(artifact_dir, "development_result")
    dev_index.write_text("# History", encoding="utf-8")
    analysis_hist_dir = history_dir_for_artifact(artifact_dir, "development_analysis_decision")
    analysis_hist_dir.mkdir(parents=True, exist_ok=True)
    analysis_archived = analysis_hist_dir / "20260506T120000_development_analysis_decision.md"
    analysis_archived.write_text(
        "---\ntype: development_analysis_decision\n---\n", encoding="utf-8"
    )
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

    assert not plan_archived.exists(), "plan archive must be removed"
    assert not plan_index.exists(), "plan history index must be removed"
    assert analysis_archived.exists(), "development_analysis_decision archive must be preserved"
    assert analysis_index.exists(), "development_analysis_decision history index must be preserved"
    assert not dev_archived.exists(), "development_result archive must be removed"
    assert not dev_index.exists(), "development_result history index must be removed"


def test_planning_analysis_bypass_clears_history_per_policy(
    tmp_path: Path,
) -> None:
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

    artifact_dir = tmp_path / ".agent" / "artifacts"
    plan_hist_dir = history_dir_for_artifact(artifact_dir, "plan")
    plan_hist_dir.mkdir(parents=True, exist_ok=True)
    plan_archived = plan_hist_dir / "20260506T120000_plan.md"
    plan_archived.write_text("---\ntype: plan\n---\n", encoding="utf-8")
    plan_index = history_index_path(artifact_dir, "plan")
    plan_index.write_text("# History", encoding="utf-8")

    analysis_hist_dir = history_dir_for_artifact(artifact_dir, "planning_analysis_decision")
    analysis_hist_dir.mkdir(parents=True, exist_ok=True)
    analysis_archived = analysis_hist_dir / "20260506T120000_planning_analysis_decision.md"
    analysis_archived.write_text("---\ntype: planning_analysis_decision\n---\n", encoding="utf-8")
    analysis_index = history_index_path(artifact_dir, "planning_analysis_decision")
    analysis_index.write_text("# History", encoding="utf-8")

    dev_hist_dir = history_dir_for_artifact(artifact_dir, "development_result")
    dev_hist_dir.mkdir(parents=True, exist_ok=True)
    dev_archived = dev_hist_dir / "20260506T120000_development_result.md"
    dev_archived.write_text("---\ntype: development_result\n---\n", encoding="utf-8")
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
            previous_phase="planning",
        ),
    )

    assert not plan_archived.exists(), "plan archive must be removed on bypass"
    assert not plan_index.exists(), "plan history index must be removed on bypass"
    assert analysis_archived.exists(), "planning_analysis_decision archive must be preserved"
    assert analysis_index.exists(), "planning_analysis_decision history index must be preserved"
    assert not dev_archived.exists(), "development_result archive must be removed on bypass"
    assert not dev_index.exists(), "development_result history index must be removed on bypass"


def test_development_analysis_bypass_clears_history_per_policy(
    tmp_path: Path,
) -> None:
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

    artifact_dir = tmp_path / ".agent" / "artifacts"
    plan_hist_dir = history_dir_for_artifact(artifact_dir, "plan")
    plan_hist_dir.mkdir(parents=True, exist_ok=True)
    plan_archived = plan_hist_dir / "20260506T120000_plan.md"
    plan_archived.write_text("---\ntype: plan\n---\n", encoding="utf-8")
    plan_index = history_index_path(artifact_dir, "plan")
    plan_index.write_text("# History", encoding="utf-8")

    dev_hist_dir = history_dir_for_artifact(artifact_dir, "development_result")
    dev_hist_dir.mkdir(parents=True, exist_ok=True)
    dev_archived = dev_hist_dir / "20260506T120000_development_result.md"
    dev_archived.write_text("---\ntype: development_result\n---\n", encoding="utf-8")
    dev_index = history_index_path(artifact_dir, "development_result")
    dev_index.write_text("# History", encoding="utf-8")

    analysis_hist_dir = history_dir_for_artifact(artifact_dir, "development_analysis_decision")
    analysis_hist_dir.mkdir(parents=True, exist_ok=True)
    analysis_archived = analysis_hist_dir / "20260506T120000_development_analysis_decision.md"
    analysis_archived.write_text(
        "---\ntype: development_analysis_decision\n---\n", encoding="utf-8"
    )
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
            previous_phase="development",
        ),
    )

    assert not plan_archived.exists(), "plan archive must be removed on bypass"
    assert not plan_index.exists(), "plan history index must be removed on bypass"
    assert analysis_archived.exists(), "development_analysis_decision archive must be preserved"
    assert analysis_index.exists(), "development_analysis_decision history index must be preserved"
    assert not dev_archived.exists(), "development_result archive must be removed on bypass"
    assert not dev_index.exists(), "development_result history index must be removed on bypass"
