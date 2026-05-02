from __future__ import annotations

import json
from typing import TYPE_CHECKING

from git import Repo as GitRepo

import ralph.prompts.materialize as materialize_module
from ralph.pipeline.cycle_baseline import write_cycle_baseline
from ralph.pipeline.runner import _clear_fresh_planning_files_if_needed
from ralph.policy.loader import load_policy
from ralph.prompts.materialize import materialize_prompt_for_phase, prompt_file_for_phase
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.memory import MemoryWorkspace

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
    "- Parallelization safety: any parallel work remains disjoint, realistic, "
    "and policy-compliant"
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


def test_materialize_prompt_for_phase_renders_planning_prompt_to_agent_tmp(tmp_path: Path) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Plan the template migration")

    prompt_path = materialize_prompt_for_phase(
        phase="planning",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
        workspace_root=tmp_path,
    )

    assert prompt_path == ".agent/tmp/planning_prompt.md"
    rendered = workspace.read(prompt_path)
    assert "PLANNING MODE" in rendered
    assert "PLANNING EDIT MODE" not in rendered
    current_prompt_path = tmp_path / ".agent" / "CURRENT_PROMPT.md"
    assert str(current_prompt_path) in rendered
    assert current_prompt_path.read_text(encoding="utf-8") == "Plan the template migration"


def test_materialize_fresh_planning_clears_previous_plan_context(tmp_path: Path) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Create a brand new plan")
    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": "Old finalized plan.",
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    },
                    "steps": [{"number": 1, "title": "Old", "content": "old step"}],
                    "critical_files": {
                        "primary_files": [{"path": "src/old.py", "action": "modify"}],
                    },
                    "risks_mitigations": [{"risk": "drift", "mitigation": "delete it"}],
                    "verification_strategy": [
                        {"method": "pytest", "expected_outcome": "passes"}
                    ],
                },
            }
        ),
    )
    workspace.write(
        ".agent/artifacts/.plan_draft.json",
        json.dumps(
            {
                "schema_version": 1,
                "started_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "sections": {
                    "summary": {
                        "context": "Old draft plan.",
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    }
                },
            }
        ),
    )
    workspace.write(".agent/PLAN.md", "# Implementation Plan\n\nOld plan handoff.\n")
    prompt_path = materialize_prompt_for_phase(
        phase="planning",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    assert "PLANNING MODE" in rendered
    assert "PLANNING EDIT MODE" not in rendered
    assert "Old finalized plan." not in rendered
    assert str(tmp_path / ".agent" / "PLAN.md") not in rendered
    assert workspace.exists(".agent/artifacts/plan.json") is False
    assert workspace.exists(".agent/artifacts/.plan_draft.json") is False
    assert workspace.exists(".agent/PLAN.md") is False


def test_clear_fresh_planning_files_removes_stale_plan_and_analysis_artifacts(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = FsWorkspace(tmp_path)
    (tmp_path / ".agent" / "artifacts").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".agent" / "artifacts" / "plan.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".agent" / "artifacts" / ".plan_draft.json").write_text(
        "{}",
        encoding="utf-8",
    )
    (tmp_path / ".agent" / "PLAN.md").write_text("old plan", encoding="utf-8")
    (
        tmp_path / ".agent" / "artifacts" / "planning_analysis_decision.json"
    ).write_text("{}", encoding="utf-8")
    (tmp_path / ".agent" / "PLANNING_ANALYSIS_DECISION.md").write_text(
        "old feedback",
        encoding="utf-8",
    )

    _clear_fresh_planning_files_if_needed(
        workspace,
        "planning",
        None,
        policy.pipeline,
        policy.artifacts,
    )

    assert (tmp_path / ".agent" / "artifacts" / "plan.json").exists() is False
    assert (tmp_path / ".agent" / "artifacts" / ".plan_draft.json").exists() is False
    assert (tmp_path / ".agent" / "PLAN.md").exists() is False
    assert (
        tmp_path / ".agent" / "artifacts" / "planning_analysis_decision.json"
    ).exists() is False
    assert (tmp_path / ".agent" / "PLANNING_ANALYSIS_DECISION.md").exists() is False



def test_clear_fresh_planning_files_preserves_loopback_plan_and_feedback(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = FsWorkspace(tmp_path)
    (tmp_path / ".agent" / "artifacts").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".agent" / "artifacts" / "plan.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".agent" / "artifacts" / ".plan_draft.json").write_text(
        "{}",
        encoding="utf-8",
    )
    (tmp_path / ".agent" / "PLAN.md").write_text("old plan", encoding="utf-8")
    (
        tmp_path / ".agent" / "artifacts" / "planning_analysis_decision.json"
    ).write_text("{}", encoding="utf-8")
    (tmp_path / ".agent" / "PLANNING_ANALYSIS_DECISION.md").write_text(
        "old feedback",
        encoding="utf-8",
    )

    _clear_fresh_planning_files_if_needed(
        workspace,
        "planning",
        "planning_analysis",
        policy.pipeline,
        policy.artifacts,
    )

    assert (tmp_path / ".agent" / "artifacts" / "plan.json").exists() is True
    assert (tmp_path / ".agent" / "artifacts" / ".plan_draft.json").exists() is True
    assert (tmp_path / ".agent" / "PLAN.md").exists() is True
    assert (
        tmp_path / ".agent" / "artifacts" / "planning_analysis_decision.json"
    ).exists() is True
    assert (tmp_path / ".agent" / "PLANNING_ANALYSIS_DECISION.md").exists() is True



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
                    "steps": [{"number": 1, "title": "Revise", "content": "keep context"}],
                    "critical_files": {
                        "primary_files": [{"path": "src/plan.py", "action": "modify"}],
                    },
                    "risks_mitigations": [{"risk": "drift", "mitigation": "revise carefully"}],
                    "verification_strategy": [
                        {"method": "pytest", "expected_outcome": "passes"}
                    ],
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
        phase="planning",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    assert "PLANNING EDIT MODE" in rendered
    assert str(tmp_path / ".agent" / "PLAN.md") in rendered
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


def test_prompt_file_for_phase_uses_agent_tmp_file_name() -> None:
    assert prompt_file_for_phase("review_analysis") == ".agent/tmp/review_analysis_prompt.md"


def test_materialize_commit_phase_tolerates_empty_diff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))

    monkeypatch.setattr(materialize_module, "_git_diff", lambda _workspace_root: "")

    prompt_path = materialize_prompt_for_phase(
        phase="development_commit",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.COMMIT),
        workspace_root=tmp_path,
    )

    assert prompt_path == ".agent/tmp/development_commit_prompt.md"
    rendered = workspace.read(prompt_path)
    assert "DIFF:" in rendered


def test_materialize_commit_phase_with_claude_prefix_includes_both_tool_aliases(
    tmp_path: Path,
    monkeypatch,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))

    monkeypatch.setattr(
        materialize_module,
        "_git_diff",
        lambda _workspace_root: "diff --git a/app.py b/app.py\n+hello",
    )

    prompt_path = materialize_prompt_for_phase(
        phase="development_commit",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(
            SessionDrain.COMMIT,
            tool_name_prefix="mcp__ralph__",
        ),
        workspace_root=tmp_path,
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
                    "steps": [
                        {
                            "number": 1,
                            "title": "step",
                            "content": "do it",
                            "step_type": "file_change",
                            "priority": "high",
                            "targets": [],
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
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(
            SessionDrain.DEVELOPMENT,
            tool_name_prefix="mcp__ralph__",
        ),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    assert "`mcp__ralph__write_file` or bare `write_file`" in rendered
    assert "`mcp__ralph__exec` or bare `exec`" in rendered
    assert "`mcp__ralph__report_progress` or bare `report_progress`" in rendered
    assert "`mcp__ralph__declare_complete` or bare `declare_complete`" in rendered


def test_materialize_development_prompt_reads_agent_plan_markdown_handoff(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement unattended planning recovery")
    workspace.write(
        ".agent/PLAN.md",
        "# Implementation Plan\n\n## Steps\n1. Add regression tests\n2. Fix pipeline routing\n",
    )

    prompt_path = materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    current_prompt_path = tmp_path / ".agent" / "CURRENT_PROMPT.md"
    assert str(current_prompt_path) in rendered
    assert str(tmp_path / ".agent" / "PLAN.md") in rendered
    assert "Read the complete implementation plan from file at" in rendered
    assert "Add regression tests" not in rendered


def test_materialize_development_prompt_uses_analysis_feedback_handoff(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement the feature")
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
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    assert str(tmp_path / ".agent" / "DEVELOPMENT_ANALYSIS_DECISION.md") in rendered
    assert "Read the complete analysis feedback from file at" in rendered
    assert "This file is the authoritative source for analysis feedback in this prompt." in rendered
    assert "Need another iteration." not in rendered


def test_materialize_development_analysis_uses_markdown_result_handoff(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Analyze the implementation")
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
        phase="development_analysis",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
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
        phase="planning_analysis",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    assert str(tmp_path / ".agent" / "PLAN.md") in rendered
    assert "Read the complete latest artifact from file at" in rendered
    assert "Fresh plan context." not in rendered


def test_materialize_fix_prompt_uses_markdown_issues_handoff(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Apply the fixes")
    workspace.write(
        ".agent/artifacts/issues.json",
        json.dumps(
            {
                "type": "issues",
                "content": {
                    "status": "issues_found",
                    "summary": "Review found gaps.",
                    "issues": [
                        {
                            "path": "ralph/pipeline/runner.py",
                            "severity": "high",
                            "summary": "Need better visibility.",
                        }
                    ],
                    "what_came_up_short": ["User cannot see the review handoff."],
                    "how_to_fix": ["Mirror issues.json to ISSUES.md."],
                },
            }
        ),
    )

    prompt_path = materialize_prompt_for_phase(
        phase="fix",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.FIX),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    assert str(tmp_path / ".agent" / "ISSUES.md") in rendered
    assert "Read the complete issues from file at" in rendered
    assert "Need better visibility." not in rendered


def test_materialize_fix_prompt_uses_review_analysis_feedback_handoff(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Apply the fixes")
    workspace.write(
        ".agent/artifacts/review_analysis_decision.json",
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
    )

    prompt_path = materialize_prompt_for_phase(
        phase="fix",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.FIX),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    assert str(tmp_path / ".agent" / "REVIEW_ANALYSIS_DECISION.md") in rendered
    assert "Read the complete analysis feedback from file at" in rendered
    assert "Fixes are required." not in rendered


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
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    assert str(tmp_path / ".agent" / "PLAN.md") in rendered
    assert "Read the complete implementation plan from file at" in rendered
    assert (
        "This file is the authoritative source for implementation plan in this prompt." in rendered
    )
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
        phase="planning",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    payload_path = tmp_path / ".agent" / "tmp" / "prompt_payloads" / "planning_prompt.txt"
    assert str(tmp_path / ".agent" / "CURRENT_PROMPT.md") in rendered
    assert large_prompt not in rendered
    assert (tmp_path / ".agent" / "CURRENT_PROMPT.md").read_text(encoding="utf-8") == large_prompt
    assert not payload_path.exists()


def test_materialize_review_prompt_uses_file_reference_for_large_diff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Review the changes")
    large_diff = "D" * (100 * 1024 + 1)

    monkeypatch.setattr(materialize_module, "_git_diff", lambda _workspace_root: large_diff)

    prompt_path = materialize_prompt_for_phase(
        phase="review",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.REVIEW),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    payload_path = tmp_path / ".agent" / "tmp" / "prompt_payloads" / "review_diff.txt"
    assert "read the complete changes from file at" in rendered.lower()
    assert (tmp_path / ".agent" / "CURRENT_PROMPT.md").read_text(encoding="utf-8") == (
        "Review the changes"
    )
    assert large_diff not in rendered
    assert payload_path.read_text(encoding="utf-8") == large_diff


def test_git_diff_uses_start_commit_sha_when_present(tmp_git_repo: Path) -> None:
    repo = GitRepo(tmp_git_repo)
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


def test_git_diff_falls_back_to_head_when_start_commit_absent(tmp_git_repo: Path) -> None:
    repo = GitRepo(tmp_git_repo)
    uncommitted = tmp_git_repo / "work.py"
    uncommitted.write_text("y = 2\n")
    repo.index.add(["work.py"])

    diff = materialize_module._git_diff(tmp_git_repo)

    assert "work.py" in diff


def test_git_diff_cumulative_across_multiple_mid_cycle_commits(tmp_git_repo: Path) -> None:
    repo = GitRepo(tmp_git_repo)
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

def test_git_diff_zero_mid_cycle_commits_only_uncommitted(tmp_git_repo: Path) -> None:
    repo = GitRepo(tmp_git_repo)
    baseline_sha = repo.head.commit.hexsha
    write_cycle_baseline(tmp_git_repo, baseline_sha)
    (tmp_git_repo / "uncommitted.py").write_text("u = 99\n")
    repo.index.add(["uncommitted.py"])
    diff = materialize_module._git_diff(tmp_git_repo)
    assert "uncommitted.py" in diff


def test_git_diff_many_mid_cycle_commits_no_uncommitted(tmp_git_repo: Path) -> None:
    repo = GitRepo(tmp_git_repo)
    baseline_sha = repo.head.commit.hexsha
    (tmp_git_repo / "commit_only_a.py").write_text("a = 1\n")
    repo.index.add(["commit_only_a.py"])
    repo.index.commit("mid-cycle commit A")
    (tmp_git_repo / "commit_only_b.py").write_text("b = 2\n")
    repo.index.add(["commit_only_b.py"])
    repo.index.commit("mid-cycle commit B")
    write_cycle_baseline(tmp_git_repo, baseline_sha)
    diff = materialize_module._git_diff(tmp_git_repo)
    assert "commit_only_a.py" in diff
    assert "commit_only_b.py" in diff


def test_git_diff_strips_lone_surrogates_from_gitpython_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    surrogate_diff = "diff --git a/file.txt b/file.txt\n@@\n+\udca4 byte\n"

    class _FakeGit:
        def diff(self, *_args: object, **_kwargs: object) -> str:
            return surrogate_diff

    class _FakeRepo:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.git = _FakeGit()

    monkeypatch.setattr(materialize_module, "Repo", _FakeRepo)

    diff = materialize_module._git_diff(tmp_path)

    assert "\udca4" not in diff
    diff.encode("utf-8")  # must not raise


def test_materialize_commit_phase_handles_surrogate_diff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))

    surrogate_diff = "diff --git a/x.py b/x.py\n+\udca4\n"
    monkeypatch.setattr(
        materialize_module,
        "_git_diff",
        lambda _workspace_root: surrogate_diff,
    )

    prompt_path = materialize_prompt_for_phase(
        phase="development_commit",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.COMMIT),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    assert "\udca4" not in rendered
    rendered.encode("utf-8")  # must not raise


def test_materialize_commit_phase_with_oversized_surrogate_diff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))

    big_surrogate = "\udca4" + ("x" * (100 * 1024 + 1))
    monkeypatch.setattr(
        materialize_module,
        "_git_diff",
        lambda _workspace_root: big_surrogate,
    )

    prompt_path = materialize_prompt_for_phase(
        phase="development_commit",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.COMMIT),
        workspace_root=tmp_path,
    )

    rendered = workspace.read(prompt_path)
    assert "\udca4" not in rendered
    payload_path = tmp_path / ".agent" / "tmp" / "prompt_payloads" / "development_commit_diff.txt"
    assert payload_path.exists()
    written = payload_path.read_text(encoding="utf-8")
    assert "\udca4" not in written
