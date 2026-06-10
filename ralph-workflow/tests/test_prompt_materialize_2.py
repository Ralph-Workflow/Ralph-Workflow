from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from git import Repo as GitRepo

import ralph.prompts.materialize as materialize_module
from ralph.mcp.artifacts.history import (
    history_index_path,
)
from ralph.pipeline.cycle_baseline import write_cycle_baseline
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
)
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
    prompt_file_for_phase,
)
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace


class _ArtifactSubmitSession:
    session_id = "test-session"
    drain = "planning_analysis"

    def check_capability(self, capability: str) -> object:
        return capability == "artifact.submit"


PLANNING_EDIT_GET_DRAFT_TEXT = (
    "Use `ralph_get_plan_draft` to inspect the current finalized plan "
    "or staged draft before editing."
)
PLANNING_EDIT_DEFECT_SCOPE_TEXT = (
    "Before revising any section, classify the feedback scope as one of:"
)
PLANNING_EDIT_GLOBAL_REDERIVATION_TEXT = (
    "If any feedback item reveals repo-wide incompleteness, invalid inventory, incorrect paths, "
    "narrow verification, or prompt-to-plan traceability gaps, you MUST re-derive the plan"
)
PLANNING_EDIT_FINALIZE_TEXT = (
    "Use `ralph_finalize_plan` after revising the affected sections so "
    "the updated plan replaces the prior finalized plan."
)
PLANNING_EDIT_SELF_AUDIT_TEXT = "Before `ralph_finalize_plan`, perform this self-audit:"
PLANNING_EDIT_RISK_COVERAGE_TEXT = (
    "- Risk coverage: concrete risks, mitigations, and edge cases are represented"
)
PLANNING_EDIT_PARALLELIZATION_TEXT = (
    "- Parallelization safety: any parallel work remains disjoint, realistic, and policy-compliant"
)
PLANNING_EDIT_MAINTAINABILITY_TEXT = (
    "- Maintainability and handoff quality: the plan stays concise, "
    "non-redundant, and explicit for development handoff"
)
PLANNING_EDIT_SCOPE_INVALIDATION_TEXT = (
    "If the ORIGINAL REQUEST has repository-wide acceptance criteria and the current plan "
    "narrowed scope before running repository-wide discovery"
)
PLANNING_EDIT_DISCOVERY_FIRST_TEXT = (
    "replace the summary, scope, and early steps so Step 1 becomes repo-wide discovery"
)
PLANNING_EDIT_SCOPE_DERIVATION_TEXT = (
    "- Scope derivation: when the task is repo-wide, implementation scope comes from an "
    "explicit repo-wide discovery step rather than a guessed subsystem"
)
PLANNING_EDIT_PASS_TARGET_TEXT = (
    "Your target is to submit the strongest revised plan you can so the next planning-analysis pass"
)
PLANNING_EDIT_NO_KNOWN_GAPS_TEXT = (
    "Do not finalize a draft that still has any known unresolved analyzer finding"
)
PLANNING_EDIT_DEPENDENT_SECTION_REWRITE_TEXT = (
    "If fixing one section changes the truth of another section, replace every dependent section"
)
PLANNING_EDIT_NEXT_ANALYZER_TEXT = (
    "Before finalizing, proactively search for any additional repo-grounded failure"
)
PLANNING_EDIT_SURFACED_BLOCKER_TEXT = (
    "If a canonical verification command or repo-wide audit already surfaces a blocker "
    "during replanning"
)
PLANNING_EDIT_RULE_CATEGORY_TEXT = (
    "When the ORIGINAL REQUEST imposes repo-wide structural rules, build a repo-wide inventory"
)
PLANNING_EDIT_NO_EXCEPTION_TEXT = (
    "Do not preserve prompt-violating tests, files, or workflows as justified exceptions"
)
PLANNING_EDIT_STARTING_POINT_TEXT = (
    "Treat the planning-analysis feedback as a starting point, not as the full list of issues"
)
PLANNING_EDIT_NOT_LOCAL_PATCH_TEXT = (
    "Do not localize your revision pass to only the sections explicitly cited by the analyzer"
)
PLANNING_EDIT_SELF_ANALYSIS_TEXT = (
    "You must perform your own repo-grounded analysis before finalizing"
)
PLANNING_EDIT_ISSUE_MAPPING_TEXT = (
    "Every analyzer issue must map to concrete revised sections or an explicit verified reason"
)
PLANNING_ANALYSIS_MCP_REMEDIATION_TEXT = (
    "When describing remediation, target the planner's MCP revision workflow"
)
PLANNING_ANALYSIS_SECTION_RESUBMIT_TEXT = (
    "Exact plan sections to resubmit via the MCP plan-edit tools."
)

if TYPE_CHECKING:
    from pathlib import Path


MINIMAL_PLAN_HANDOFF = (
    "# Execution Plan\n\n"
    "1. Add regression coverage.\n"
    "2. Tighten non-planning prompt preconditions.\n"
)


def _write_plan_handoff(workspace: MemoryWorkspace) -> None:
    workspace.write(".agent/PLAN.md", MINIMAL_PLAN_HANDOFF)


def test_planning_retry_prompt_includes_artifact_history_path_when_history_exists(
    tmp_path: Path,
) -> None:

    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Revise the failed plan with history")
    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": "Retryable plan context.",
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    },
                    "skills_mcp": {
                        "skills": [
                            "test-driven-development",
                            "verification-before-completion",
                        ],
                        "mcps": [],
                    },
                    "steps": [{"number": 1, "title": "Revise", "content": "keep retry context"}],
                    "critical_files": {
                        "primary_files": [{"path": "src/plan.py", "action": "modify"}],
                    },
                    "risks_mitigations": [{"risk": "drift", "mitigation": "preserve"}],
                    "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
                },
            }
        ),
    )
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\nRetryable plan context.\n")
    workspace.write(
        ".agent/tmp/last_retry_error_planning.txt",
        "PREVIOUS ATTEMPT FAILED: validation error during planning retry",
    )
    artifact_dir = tmp_path / ".agent" / "artifacts"
    plan_history_file = history_index_path(artifact_dir, "plan")
    plan_history_file.parent.mkdir(parents=True, exist_ok=True)
    plan_history_file.write_text("# Planning History\n\n## Entry 1\n", encoding="utf-8")
    development_history_file = history_index_path(artifact_dir, "development_result")
    development_history_file.parent.mkdir(parents=True, exist_ok=True)
    development_history_file.write_text("# Development History\n\n## Entry 1\n", encoding="utf-8")

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="planning",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
            previous_phase="planning",
        ),
    )

    rendered = workspace.read(prompt_path)
    assert "ARTIFACT HISTORY" in rendered
    assert str(plan_history_file) in rendered


def test_materialize_planning_loopback_uses_edit_prompt_and_analysis_feedback_handoff(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Revise the pipeline plan")
    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": "Existing plan to revise.",
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    },
                    "skills_mcp": {
                        "skills": [
                            "test-driven-development",
                            "verification-before-completion",
                        ],
                        "mcps": [],
                    },
                    "steps": [{"number": 1, "title": "Revise", "content": "keep context"}],
                    "critical_files": {
                        "primary_files": [{"path": "src/plan.py", "action": "modify"}],
                    },
                    "risks_mitigations": [{"risk": "drift", "mitigation": "revise carefully"}],
                    "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
                },
            }
        ),
    )
    workspace.write(
        ".agent/artifacts/planning_analysis_decision.json",
        json.dumps(
            {
                "type": "planning_analysis_decision",
                "content": {
                    "status": "request_changes",
                    "summary": "The plan needs revisions.",
                    "what_came_up_short": ["Verification commands are too vague."],
                    "how_to_fix": ["Edit the existing plan draft instead of restarting."],
                },
            }
        ),
    )

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="planning",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
            previous_phase="planning_analysis",
        ),
    )

    rendered = workspace.read(prompt_path)
    assert "PLANNING EDIT MODE" in rendered
    assert str(tmp_path / ".agent" / "PLAN.md") not in rendered
    assert "ralph_get_plan_draft" in rendered
    assert str(tmp_path / ".agent" / "PLANNING_ANALYSIS_DECISION.md") in rendered
    assert "Read the complete analysis feedback from file at" in rendered
    assert "This file is the authoritative source for analysis feedback in this prompt." in rendered
    assert PLANNING_EDIT_GET_DRAFT_TEXT in rendered
    assert PLANNING_EDIT_DEFECT_SCOPE_TEXT in rendered
    assert PLANNING_EDIT_GLOBAL_REDERIVATION_TEXT in rendered
    assert PLANNING_EDIT_FINALIZE_TEXT in rendered
    assert PLANNING_EDIT_SELF_AUDIT_TEXT in rendered
    assert PLANNING_EDIT_RISK_COVERAGE_TEXT in rendered
    assert PLANNING_EDIT_PARALLELIZATION_TEXT in rendered
    assert PLANNING_EDIT_MAINTAINABILITY_TEXT in rendered
    assert PLANNING_EDIT_SCOPE_INVALIDATION_TEXT in rendered
    assert PLANNING_EDIT_DISCOVERY_FIRST_TEXT in rendered
    assert PLANNING_EDIT_SCOPE_DERIVATION_TEXT in rendered
    assert PLANNING_EDIT_PASS_TARGET_TEXT in rendered
    assert PLANNING_EDIT_NO_KNOWN_GAPS_TEXT in rendered
    assert PLANNING_EDIT_DEPENDENT_SECTION_REWRITE_TEXT in rendered
    assert PLANNING_EDIT_NEXT_ANALYZER_TEXT in rendered
    assert PLANNING_EDIT_SURFACED_BLOCKER_TEXT in rendered
    assert PLANNING_EDIT_RULE_CATEGORY_TEXT in rendered
    assert PLANNING_EDIT_NO_EXCEPTION_TEXT in rendered
    assert PLANNING_EDIT_STARTING_POINT_TEXT in rendered
    assert PLANNING_EDIT_NOT_LOCAL_PATCH_TEXT in rendered
    assert PLANNING_EDIT_SELF_ANALYSIS_TEXT in rendered
    assert PLANNING_EDIT_ISSUE_MAPPING_TEXT in rendered
    assert "The plan needs revisions." not in rendered
    assert workspace.exists(".agent/artifacts/plan.json") is True
    assert workspace.exists(".agent/artifacts/planning_analysis_decision.json") is True


def test_materialize_review_phase_references_plan_handoff_when_plan_exists(
    tmp_path: Path,
) -> None:
    """A custom review-role phase rendered with review.jinja must reference the plan handoff.

    When a plan.json artifact is present the prompt-materialization layer
    regenerates .agent/PLAN.md and passes its absolute path as PLAN_PATH.
    The rendered review prompt must point at that file rather than inlining the
    plan content, because review.jinja uses render_payload_section which emits a
    file-reference instruction when the path variable is non-empty.
    """
    pipeline_policy = PipelinePolicy(
        phases={
            "review": PhaseDefinition(
                drain="review",
                role="review",
                prompt_template="review.jinja",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": PhaseDefinition(
                drain="complete",
                transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
            ),
        },
        entry_phase="review",
        terminal_phase="complete",
    )
    artifacts_policy = ArtifactsPolicy(artifacts={})
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Review the implementation.")
    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": "Plan for the reviewed change.",
                        "scope_items": [{"text": "one"}, {"text": "two"}, {"text": "three"}],
                    },
                    "skills_mcp": {
                        "skills": [
                            "test-driven-development",
                            "verification-before-completion",
                        ],
                        "mcps": [],
                    },
                    "steps": [{"number": 1, "title": "Implement", "content": "do the work"}],
                    "critical_files": {
                        "primary_files": [{"path": "src/app.py", "action": "modify"}],
                    },
                    "risks_mitigations": [{"risk": "regression", "mitigation": "run tests"}],
                    "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
                },
            }
        ),
    )

    expected_plan_path = str(tmp_path / ".agent" / "PLAN.md")

    with patch.object(materialize_module, "_git_diff", return_value="diff --git a/src/app.py"):
        prompt_path = materialize_prompt_for_phase(
            PromptPhaseContext(
                phase="review",
                workspace=workspace,
                pipeline_policy=pipeline_policy,
                session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.REVIEW),
                workspace_root=tmp_path,
            ),
            PromptPhaseOptions(
                artifacts_policy=artifacts_policy,
            ),
        )

    rendered = workspace.read(prompt_path)
    assert expected_plan_path in rendered
    assert "Read the complete plan from file at" in rendered
    assert (tmp_path / ".agent" / "PLAN.md").exists()


def test_prompt_file_for_phase_uses_agent_tmp_file_name() -> None:
    assert prompt_file_for_phase("review_analysis") == ".agent/tmp/review_analysis_prompt.md"


def test_materialize_commit_phase_tolerates_empty_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))

    monkeypatch.setattr(materialize_module, "_pending_diff", lambda _workspace_root: "")

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development_commit",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.COMMIT),
            workspace_root=tmp_path,
        ),
    )

    assert prompt_path == ".agent/tmp/development_commit_prompt.md"
    rendered = workspace.read(prompt_path)
    assert "DIFF:" in rendered


def test_materialize_commit_phase_with_claude_prefix_includes_both_tool_aliases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))

    monkeypatch.setattr(
        materialize_module,
        "_pending_diff",
        lambda _workspace_root: "diff --git a/app.py b/app.py\n+hello",
    )

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development_commit",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(
                SessionDrain.COMMIT,
                tool_name_prefix="mcp__ralph__",
            ),
            workspace_root=tmp_path,
        ),
    )

    rendered = workspace.read(prompt_path)
    assert "mcp__ralph__ralph_submit_artifact" in rendered
    assert "or `ralph_submit_artifact`" in rendered


def test_materialize_development_phase_surfaces_bare_fallbacks_for_shared_mcp_tools(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement the feature")
    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": "ctx",
                        "scope_items": [
                            {"text": "a"},
                            {"text": "b"},
                            {"text": "c"},
                        ],
                    },
                    "skills_mcp": {
                        "skills": [
                            "test-driven-development",
                            "verification-before-completion",
                        ],
                        "mcps": [],
                    },
                    "steps": [
                        {
                            "number": 1,
                            "title": "step",
                            "content": "do it",
                            "step_type": "action",
                            "priority": "high",
                            "depends_on": [],
                        }
                    ],
                    "critical_files": {
                        "primary_files": [{"path": "src/app.py", "action": "modify"}],
                        "reference_files": [],
                    },
                    "risks_mitigations": [{"risk": "regression", "mitigation": "add tests"}],
                    "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
                    "work_units": [],
                },
            }
        ),
    )

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(
                SessionDrain.DEVELOPMENT,
                tool_name_prefix="mcp__ralph__",
            ),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
        ),
    )

    rendered = workspace.read(prompt_path)
    assert "`mcp__ralph__write_file` or bare `write_file`" in rendered
    assert "`mcp__ralph__exec` or bare `exec`" in rendered
    assert "`mcp__ralph__report_progress` or bare `report_progress`" in rendered
    assert "`mcp__ralph__declare_complete` or bare `declare_complete`" in rendered


def test_materialize_development_entry_clears_all_completed_planning_history(
    tmp_path: Path,
) -> None:

    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement unattended planning recovery")
    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": "Approved plan context.",
                        "scope_items": [{"text": "one"}, {"text": "two"}, {"text": "three"}],
                    },
                    "skills_mcp": {
                        "skills": [
                            "test-driven-development",
                            "verification-before-completion",
                        ],
                        "mcps": [],
                    },
                    "steps": [{"number": 1, "title": "Implement", "content": "do the work"}],
                    "critical_files": {
                        "primary_files": [{"path": "src/app.py", "action": "modify"}],
                        "reference_files": [],
                    },
                    "risks_mitigations": [{"risk": "regression", "mitigation": "test it"}],
                    "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
                },
            }
        ),
    )
    artifact_dir = tmp_path / ".agent" / "artifacts"
    plan_history_file = history_index_path(artifact_dir, "plan")
    plan_history_file.parent.mkdir(parents=True, exist_ok=True)
    plan_history_file.write_text("# Planning History\n\n## Entry 1\n", encoding="utf-8")
    development_history_file = history_index_path(artifact_dir, "development_result")
    development_history_file.parent.mkdir(parents=True, exist_ok=True)
    development_history_file.write_text("# Development History\n\n## Entry 1\n", encoding="utf-8")

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
            previous_phase="planning_analysis",
        ),
    )

    rendered = workspace.read(prompt_path)
    assert str(tmp_path / ".agent" / "PLAN.md") in rendered
    assert plan_history_file.exists() is False
    assert development_history_file.exists() is False


def test_materialize_development_prompt_reads_agent_plan_markdown_handoff(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement unattended planning recovery")
    workspace.write(
        ".agent/PLAN.md",
        "# Execution Plan\n\n## Steps\n1. Add regression tests\n2. Fix pipeline routing\n",
    )

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
        ),
    )

    rendered = workspace.read(prompt_path)
    current_prompt_path = tmp_path / ".agent" / "CURRENT_PROMPT.md"
    assert str(current_prompt_path) in rendered
    assert str(tmp_path / ".agent" / "PLAN.md") in rendered
    assert "Read the complete execution plan from file at" in rendered
    assert "Add regression tests" not in rendered


def test_materialize_development_prompt_uses_analysis_feedback_handoff(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement the feature")
    _write_plan_handoff(workspace)
    workspace.write(
        ".agent/artifacts/development_analysis_decision.json",
        json.dumps(
            {
                "type": "development_analysis_decision",
                "content": {
                    "status": "request_changes",
                    "summary": "Need another iteration.",
                    "what_came_up_short": ["Feedback is hidden from the developer."],
                    "how_to_fix": ["Read the analysis handoff before editing."],
                },
            }
        ),
    )

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
        ),
    )

    rendered = workspace.read(prompt_path)
    assert str(tmp_path / ".agent" / "DEVELOPMENT_ANALYSIS_DECISION.md") in rendered
    assert "Read the complete analysis feedback from file at" in rendered
    assert "This file is the authoritative source for analysis feedback in this prompt." in rendered
    assert "Need another iteration." not in rendered


@pytest.mark.parametrize("analysis_iteration", [2, 3, 4])
def test_repeated_development_loopback_never_renders_fresh_template(
    tmp_path: Path,
    analysis_iteration: int,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write(
        "PROMPT.md",
        f"Continue implementation after development-analysis iteration {analysis_iteration}",
    )
    _write_plan_handoff(workspace)

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
            previous_phase="development_analysis",
        ),
    )

    rendered = workspace.read(prompt_path)
    assert "continuing a DEVELOPMENT iteration" in rendered
    assert "You are in IMPLEMENTATION MODE" not in rendered
    assert str(tmp_path / ".agent" / "PLAN.md") in rendered


def test_materialize_development_analysis_uses_markdown_result_handoff(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Analyze the implementation")
    _write_plan_handoff(workspace)
    workspace.write(
        ".agent/artifacts/development_result.json",
        json.dumps(
            {
                "type": "development_result",
                "content": {
                    "status": "completed",
                    "summary": "Implemented the feature.",
                    "files_changed": "- src/app.py",
                },
            }
        ),
    )

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development_analysis",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
        ),
    )

    rendered = workspace.read(prompt_path)
    assert str(tmp_path / ".agent" / "DEVELOPMENT_RESULT.md") in rendered
    assert "Read the complete latest artifact from file at" in rendered
    assert "Implemented the feature." not in rendered


def test_materialize_planning_analysis_uses_markdown_plan_handoff(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Analyze the plan")
    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": "Fresh plan context.",
                        "scope_items": [
                            {"text": "Add policy-defined planning analysis"},
                            {"text": "Keep artifacts generic"},
                            {"text": "Verify the loop end-to-end"},
                        ],
                    },
                    "skills_mcp": {
                        "skills": [
                            "test-driven-development",
                            "verification-before-completion",
                        ],
                        "mcps": [],
                    },
                    "steps": [
                        {
                            "number": 1,
                            "title": "Add policy",
                            "content": "Introduce a planning analysis phase before development.",
                            "step_type": "file_change",
                            "priority": "high",
                            "targets": [
                                {
                                    "path": "ralph/policy/defaults/pipeline.toml",
                                    "action": "modify",
                                }
                            ],
                            "depends_on": [],
                        }
                    ],
                    "critical_files": {
                        "primary_files": [
                            {
                                "path": "ralph/policy/defaults/pipeline.toml",
                                "action": "modify",
                            }
                        ],
                        "reference_files": [],
                    },
                    "risks_mitigations": [
                        {
                            "risk": "Hardcoded analysis artifact handling",
                            "mitigation": "Generalize it first",
                        }
                    ],
                    "verification_strategy": [
                        {
                            "method": "pytest tests/test_policy_loader.py -q",
                            "expected_outcome": "passes",
                        }
                    ],
                    "work_units": [],
                },
            }
        ),
    )

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="planning_analysis",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.ANALYSIS),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
        ),
    )

    rendered = workspace.read(prompt_path)
    assert str(tmp_path / ".agent" / "PLAN.md") not in rendered
    assert "Read the complete latest artifact from file at" not in rendered
    assert "Fresh plan context." not in rendered
    assert "ralph_get_plan_draft" in rendered


def test_materialize_development_prefers_structured_plan_artifact_over_plan_md(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement unattended planning recovery")
    workspace.write(".agent/PLAN.md", "STALE PLAN")
    workspace.write(
        ".agent/artifacts/plan.json",
        (
            '{"type":"plan","content":'
            '{"summary":{"context":"Fresh structured plan","scope_items":['
            '{"text":"Use policy-driven handoff"},'
            '{"text":"Reject missing plan artifacts"},'
            '{"text":"Expose the handoff to users"}]},'
            '"skills_mcp":{"skills":["test-driven-development"],"mcps":[]},'
            '"steps":[{"number":1,"title":"Honor policy","content":"Route via policy bundle"}],'
            '"critical_files":{"primary_files":['
            '{"path":"ralph/pipeline/runner.py","action":"modify"}]},'
            '"risks_mitigations":['
            '{"risk":"Stale markdown handoff",'
            '"mitigation":"Rewrite PLAN.md from plan.json"}],'
            '"verification_strategy":['
            '{"method":"pytest",'
            '"expected_outcome":"development prompt uses PLAN.md handoff"}]}}'
        ),
    )

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
        ),
    )

    rendered = workspace.read(prompt_path)
    assert str(tmp_path / ".agent" / "PLAN.md") in rendered
    assert "Read the complete execution plan from file at" in rendered
    assert "This file is the authoritative source for execution plan in this prompt." in rendered
    assert "STALE PLAN" not in rendered
    assert "Fresh structured plan" not in rendered
    assert (tmp_path / ".agent" / "PLAN.md").read_text(encoding="utf-8") != "STALE PLAN"
    assert "Fresh structured plan" in (tmp_path / ".agent" / "PLAN.md").read_text(encoding="utf-8")
    assert (tmp_path / ".agent" / "CURRENT_PROMPT.md").read_text(encoding="utf-8") == (
        "Implement unattended planning recovery"
    )


def test_materialize_planning_prompt_uses_file_reference_for_large_prompt(tmp_path: Path) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    large_prompt = "P" * (100 * 1024 + 1)
    workspace.write("PROMPT.md", large_prompt)

    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="planning",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
        ),
    )

    rendered = workspace.read(prompt_path)
    payload_path = tmp_path / ".agent" / "tmp" / "prompt_payloads" / "planning_prompt.txt"
    assert str(tmp_path / ".agent" / "CURRENT_PROMPT.md") in rendered
    assert large_prompt not in rendered
    assert (tmp_path / ".agent" / "CURRENT_PROMPT.md").read_text(encoding="utf-8") == large_prompt
    assert not payload_path.exists()


def test_git_diff_uses_start_commit_sha_when_present(tmp_git_repo: Path) -> None:
    with GitRepo(tmp_git_repo) as repo:
        baseline_sha = repo.head.commit.hexsha

        new_file = tmp_git_repo / "feature.py"
        new_file.write_text("x = 1\n")
        repo.index.add(["feature.py"])
        repo.index.commit("add feature")

    agent_dir = tmp_git_repo / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "start_commit").write_text(baseline_sha + "\n")

    diff = materialize_module._git_diff(tmp_git_repo)

    assert "feature.py" in diff
    repo.git.clear_cache()


def test_git_diff_falls_back_to_head_when_start_commit_absent(tmp_git_repo: Path) -> None:
    with GitRepo(tmp_git_repo) as repo:
        uncommitted = tmp_git_repo / "work.py"
        uncommitted.write_text("y = 2\n")
        repo.index.add(["work.py"])

    diff = materialize_module._git_diff(tmp_git_repo)

    assert "work.py" in diff
    repo.git.clear_cache()


def test_git_diff_cumulative_across_multiple_mid_cycle_commits(tmp_git_repo: Path) -> None:
    with GitRepo(tmp_git_repo) as repo:
        baseline_sha = repo.head.commit.hexsha
        (tmp_git_repo / "file_a.py").write_text("a = 1\n")
        repo.index.add(["file_a.py"])
        repo.index.commit("mid-cycle commit 1")
        (tmp_git_repo / "file_b.py").write_text("b = 2\n")
        repo.index.add(["file_b.py"])
        repo.index.commit("mid-cycle commit 2")
        (tmp_git_repo / "file_c.py").write_text("c = 3\n")
        repo.index.add(["file_c.py"])
    write_cycle_baseline(tmp_git_repo, baseline_sha)
    diff = materialize_module._git_diff(tmp_git_repo)
    assert "file_a.py" in diff
    assert "file_b.py" in diff
    assert "file_c.py" in diff
    repo.git.clear_cache()


def test_git_diff_zero_mid_cycle_commits_only_uncommitted(tmp_git_repo: Path) -> None:
    with GitRepo(tmp_git_repo) as repo:
        baseline_sha = repo.head.commit.hexsha
        write_cycle_baseline(tmp_git_repo, baseline_sha)
        (tmp_git_repo / "uncommitted.py").write_text("u = 99\n")
        repo.index.add(["uncommitted.py"])
    diff = materialize_module._git_diff(tmp_git_repo)
    assert "uncommitted.py" in diff
    repo.git.clear_cache()
