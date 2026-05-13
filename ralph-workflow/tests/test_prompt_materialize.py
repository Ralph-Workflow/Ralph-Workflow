from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from git import Repo as GitRepo

import ralph.prompts.materialize as materialize_module
from ralph.pipeline.cycle_baseline import write_cycle_baseline
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
)
from ralph.prompts.debug_dump import multimodal_sidecar_path
from ralph.prompts.materialize import (
    MultimodalSidecarEntry,
    collect_media_entries_for_phase,
    materialize_prompt_for_phase,
    prompt_file_for_phase,
)
from ralph.prompts.template_engine import TemplateRenderingError
from ralph.prompts.types import SessionCapabilities, SessionDrain
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
    "# Implementation Plan\n\n"
    "1. Add regression coverage.\n"
    "2. Tighten non-planning prompt preconditions.\n"
)


def _write_plan_handoff(workspace: MemoryWorkspace) -> None:
    workspace.write(".agent/PLAN.md", MINIMAL_PLAN_HANDOFF)


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
        previous_phase=None,
    )

    assert prompt_path == ".agent/tmp/planning_prompt.md"
    rendered = workspace.read(prompt_path)
    assert "PLANNING MODE" in rendered
    assert "PLANNING EDIT MODE" not in rendered
    current_prompt_path = tmp_path / ".agent" / "CURRENT_PROMPT.md"
    assert str(current_prompt_path) in rendered
    assert current_prompt_path.read_text(encoding="utf-8") == "Plan the template migration"


def test_fresh_planning_prompt_does_not_include_artifact_history_even_if_history_exists(
    tmp_path: Path,
) -> None:
    from ralph.mcp.artifacts.history import history_index_path

    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Create a brand new plan")
    history_file = history_index_path(tmp_path / ".agent" / "artifacts", "plan")
    history_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text("# Planning History\n\n## Entry 1\n", encoding="utf-8")

    prompt_path = materialize_prompt_for_phase(
        phase="planning",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
        workspace_root=tmp_path,
        previous_phase=None,
    )

    rendered = workspace.read(prompt_path)
    assert "ARTIFACT HISTORY" not in rendered
    assert str(history_file) not in rendered
    assert history_file.exists() is False


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
                    "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
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
    workspace.write(
        ".agent/artifacts/planning_analysis_decision.json",
        json.dumps(
            {
                "type": "planning_analysis_decision",
                "content": {
                    "status": "request_changes",
                    "summary": "Old planning analysis feedback.",
                    "what_came_up_short": ["Old issue"],
                    "how_to_fix": ["Old remediation"],
                },
            }
        ),
    )
    workspace.write(
        ".agent/PLANNING_ANALYSIS_DECISION.md",
        "# Planning Analysis Decision\n\nOld planning analysis handoff.\n",
    )
    prompt_path = materialize_prompt_for_phase(
        phase="planning",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
        workspace_root=tmp_path,
        previous_phase=None,
    )

    rendered = workspace.read(prompt_path)
    assert "PLANNING MODE" in rendered
    assert "PLANNING EDIT MODE" not in rendered
    assert "Old finalized plan." not in rendered
    assert "Old planning analysis feedback." not in rendered
    assert str(tmp_path / ".agent" / "PLAN.md") not in rendered
    assert str(tmp_path / ".agent" / "PLANNING_ANALYSIS_DECISION.md") not in rendered
    assert workspace.exists(".agent/artifacts/plan.json") is False
    assert workspace.exists(".agent/artifacts/.plan_draft.json") is False
    assert workspace.exists(".agent/PLAN.md") is False
    assert workspace.exists(".agent/artifacts/planning_analysis_decision.json") is False
    assert workspace.exists(".agent/PLANNING_ANALYSIS_DECISION.md") is False


def test_materialize_planning_fallback_tolerates_missing_plan_and_clears_stale_artifacts(
    tmp_path: Path,
) -> None:
    """Regression: planning_fallback.jinja must tolerate missing plan handoff and clear stale state.

    When render_template raises TemplateRenderingError for planning.jinja during fresh
    planning, prompt_planning_xml_with_context falls back to planning_fallback.jinja.
    This fallback is a fresh-planning path that must tolerate missing plan handoff
    (like planning.jinja) and must clear stale plan/analysis artifacts.
    """
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Plan the MCP hardening")

    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": "Stale plan that should be cleared.",
                        "scope_items": [{"text": "old item"}],
                    },
                    "steps": [{"number": 1, "title": "Old", "content": "old step"}],
                    "critical_files": {
                        "primary_files": [{"path": "src/old.py", "action": "delete"}],
                    },
                    "risks_mitigations": [{"risk": "stale", "mitigation": "remove"}],
                    "verification_strategy": [{"method": "pytest", "expected_outcome": "fail"}],
                },
            }
        ),
    )
    workspace.write(".agent/PLAN.md", "# Implementation Plan\n\nStale handoff.\n")
    workspace.write(
        ".agent/artifacts/planning_analysis_decision.json",
        json.dumps(
            {
                "type": "planning_analysis_decision",
                "content": {
                    "status": "request_changes",
                    "summary": "Stale analysis feedback.",
                    "what_came_up_short": ["stale"],
                    "how_to_fix": ["clear"],
                },
            }
        ),
    )
    workspace.write(
        ".agent/PLANNING_ANALYSIS_DECISION.md",
        "# Planning Analysis Decision\n\nStale analysis handoff.\n",
    )

    with patch(
        "ralph.prompts.developer.render_template",
        side_effect=TemplateRenderingError("primary template unavailable"),
    ):
        prompt_path = materialize_prompt_for_phase(
            phase="planning",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            artifacts_policy=policy.artifacts,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
            workspace_root=tmp_path,
            previous_phase=None,
        )

    rendered = workspace.read(prompt_path)
    assert "PLANNING MODE" in rendered
    assert "PLANNING EDIT MODE" not in rendered
    assert "Stale plan that should be cleared" not in rendered
    assert "Stale analysis feedback" not in rendered
    assert "src/old.py" not in rendered
    assert str(tmp_path / ".agent" / "PLAN.md") not in rendered
    assert str(tmp_path / ".agent" / "PLANNING_ANALYSIS_DECISION.md") not in rendered
    assert workspace.exists(".agent/artifacts/plan.json") is False
    assert workspace.exists(".agent/PLAN.md") is False
    assert workspace.exists(".agent/artifacts/planning_analysis_decision.json") is False
    assert workspace.exists(".agent/PLANNING_ANALYSIS_DECISION.md") is False


def test_planning_loopback_entry_preserves_plan_and_analysis_artifacts(
    tmp_path: Path,
) -> None:
    """Planning loopback (from analysis) must not delete existing plan state."""
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Refine the plan based on analysis feedback")
    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": "Existing plan to preserve.",
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    },
                    "steps": [{"number": 1, "title": "Keep", "content": "keep existing"}],
                    "critical_files": {
                        "primary_files": [{"path": "src/keep.py", "action": "modify"}],
                    },
                    "risks_mitigations": [{"risk": "drift", "mitigation": "preserve"}],
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
                    "summary": "Please tighten the verification strategy.",
                    "what_came_up_short": ["Verification is too vague."],
                    "how_to_fix": ["Add specific pytest commands."],
                },
            }
        ),
    )

    materialize_prompt_for_phase(
        phase="planning",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
        workspace_root=tmp_path,
        previous_phase="planning_analysis",
    )

    assert workspace.exists(".agent/artifacts/plan.json") is True
    assert workspace.exists(".agent/artifacts/planning_analysis_decision.json") is True


@pytest.mark.parametrize("analysis_iteration", [2, 3, 4])
def test_repeated_planning_loopback_never_renders_fresh_template(
    tmp_path: Path,
    analysis_iteration: int,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write(
        "PROMPT.md",
        f"Revise the pipeline plan after planning-analysis iteration {analysis_iteration}",
    )
    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": (
                            f"Existing plan preserved for loopback iteration {analysis_iteration}."
                        ),
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    },
                    "steps": [
                        {
                            "number": 1,
                            "title": "Revise",
                            "content": "keep context instead of restarting",
                        }
                    ],
                    "critical_files": {
                        "primary_files": [{"path": "src/plan.py", "action": "modify"}],
                    },
                    "risks_mitigations": [{"risk": "drift", "mitigation": "revise carefully"}],
                    "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
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
        previous_phase="planning_analysis",
    )

    rendered = workspace.read(prompt_path)
    assert "PLANNING EDIT MODE" in rendered
    assert "PLANNING MODE" not in rendered
    assert "Your job is to revise the existing plan" in rendered


def test_planning_loopback_prompt_includes_artifact_history_path_when_history_exists(
    tmp_path: Path,
) -> None:
    from ralph.mcp.artifacts.history import history_index_path

    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Revise the pipeline plan with history")
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
                    "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
                },
            }
        ),
    )
    history_file = history_index_path(tmp_path / ".agent" / "artifacts", "plan")
    history_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text("# Planning History\n\n## Entry 1\n", encoding="utf-8")

    prompt_path = materialize_prompt_for_phase(
        phase="planning",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
        workspace_root=tmp_path,
        previous_phase="planning_analysis",
    )

    rendered = workspace.read(prompt_path)
    assert "ARTIFACT HISTORY" in rendered
    assert str(history_file) in rendered


def test_materialize_planning_retry_preserves_current_plan_context_when_last_retry_error_exists(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Revise the failed plan without losing current context")
    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": "Current plan that must survive retry.",
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    },
                    "steps": [{"number": 1, "title": "Revise", "content": "preserve current work"}],
                    "critical_files": {
                        "primary_files": [{"path": "src/plan.py", "action": "modify"}],
                    },
                    "risks_mitigations": [{"risk": "drift", "mitigation": "preserve"}],
                    "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
                },
            }
        ),
    )
    workspace.write(".agent/PLAN.md", "# Implementation Plan\n\nCurrent retryable plan context.\n")
    workspace.write(
        ".agent/tmp/last_retry_error_planning.txt",
        "PREVIOUS ATTEMPT FAILED: validation error during planning retry",
    )

    prompt_path = materialize_prompt_for_phase(
        phase="planning",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
        workspace_root=tmp_path,
        previous_phase="planning",
    )

    rendered = workspace.read(prompt_path)
    assert "PLANNING EDIT MODE" in rendered
    assert "PREVIOUS ATTEMPT ERROR" in rendered
    assert str(tmp_path / ".agent" / "PLAN.md") in rendered
    assert workspace.exists(".agent/artifacts/plan.json") is True
    assert workspace.exists(".agent/PLAN.md") is True


def test_materialize_resumed_planning_with_draft_only_uses_draft_context(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Resume the interrupted planning pass")
    workspace.write(
        ".agent/artifacts/.plan_draft.json",
        json.dumps(
            {
                "schema_version": 1,
                "started_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:01+00:00",
                "sections": {
                    "summary": {
                        "context": "Resumed draft-only context.",
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

    prompt_path = materialize_prompt_for_phase(
        phase="planning",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
        workspace_root=tmp_path,
        previous_phase=None,
        resume_existing_phase=True,
    )

    rendered = workspace.read(prompt_path)
    assert "PLANNING EDIT MODE" in rendered
    assert "Resumed draft-only context." in rendered
    assert workspace.exists(".agent/artifacts/.plan_draft.json") is True


def test_materialize_resumed_planning_preserves_existing_plan_context(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Resume the interrupted planning pass")
    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": "Resumed plan context.",
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    },
                    "steps": [{"number": 1, "title": "Resume", "content": "continue editing"}],
                    "critical_files": {
                        "primary_files": [{"path": "src/plan.py", "action": "modify"}],
                    },
                    "risks_mitigations": [{"risk": "drift", "mitigation": "preserve"}],
                    "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
                },
            }
        ),
    )
    workspace.write(".agent/PLAN.md", "# Implementation Plan\n\nResumed plan context.\n")
    workspace.write(
        ".agent/artifacts/.plan_draft.json",
        json.dumps(
            {
                "schema_version": 1,
                "started_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:01+00:00",
                "sections": {
                    "summary": {
                        "context": "Resumed draft context.",
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

    prompt_path = materialize_prompt_for_phase(
        phase="planning",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
        workspace_root=tmp_path,
        previous_phase=None,
        resume_existing_phase=True,
    )

    rendered = workspace.read(prompt_path)
    assert "PLANNING EDIT MODE" in rendered
    assert workspace.exists(".agent/artifacts/plan.json") is True
    assert workspace.exists(".agent/artifacts/.plan_draft.json") is True
    assert workspace.exists(".agent/PLAN.md") is True


def test_planning_retry_prompt_includes_artifact_history_path_when_history_exists(
    tmp_path: Path,
) -> None:
    from ralph.mcp.artifacts.history import history_index_path

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
    workspace.write(".agent/PLAN.md", "# Implementation Plan\n\nRetryable plan context.\n")
    workspace.write(
        ".agent/tmp/last_retry_error_planning.txt",
        "PREVIOUS ATTEMPT FAILED: validation error during planning retry",
    )
    history_file = history_index_path(tmp_path / ".agent" / "artifacts", "plan")
    history_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text("# Planning History\n\n## Entry 1\n", encoding="utf-8")

    prompt_path = materialize_prompt_for_phase(
        phase="planning",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
        workspace_root=tmp_path,
        previous_phase="planning",
    )

    rendered = workspace.read(prompt_path)
    assert "ARTIFACT HISTORY" in rendered
    assert str(history_file) in rendered


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
        phase="planning",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
        workspace_root=tmp_path,
        previous_phase="planning_analysis",
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
            phase="review",
            workspace=workspace,
            pipeline_policy=pipeline_policy,
            artifacts_policy=artifacts_policy,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.REVIEW),
            workspace_root=tmp_path,
        )

    rendered = workspace.read(prompt_path)
    assert expected_plan_path in rendered
    assert "Read the complete plan from file at" in rendered
    assert (tmp_path / ".agent" / "PLAN.md").exists()


def test_prompt_file_for_phase_uses_agent_tmp_file_name() -> None:
    assert prompt_file_for_phase("review_analysis") == ".agent/tmp/review_analysis_prompt.md"


def test_materialize_commit_phase_tolerates_empty_diff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))

    monkeypatch.setattr(materialize_module, "_pending_diff", lambda _workspace_root: "")

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
        "_pending_diff",
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
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
        previous_phase="development_analysis",
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


def test_git_diff_falls_back_to_head_when_start_commit_absent(tmp_git_repo: Path) -> None:
    with GitRepo(tmp_git_repo) as repo:
        uncommitted = tmp_git_repo / "work.py"
        uncommitted.write_text("y = 2\n")
        repo.index.add(["work.py"])

    diff = materialize_module._git_diff(tmp_git_repo)

    assert "work.py" in diff


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


def test_git_diff_zero_mid_cycle_commits_only_uncommitted(tmp_git_repo: Path) -> None:
    with GitRepo(tmp_git_repo) as repo:
        baseline_sha = repo.head.commit.hexsha
        write_cycle_baseline(tmp_git_repo, baseline_sha)
        (tmp_git_repo / "uncommitted.py").write_text("u = 99\n")
        repo.index.add(["uncommitted.py"])
    diff = materialize_module._git_diff(tmp_git_repo)
    assert "uncommitted.py" in diff


def test_pending_diff_shows_only_uncommitted_work(tmp_git_repo: Path) -> None:
    with GitRepo(tmp_git_repo) as repo:
        baseline_sha = repo.head.commit.hexsha
        write_cycle_baseline(tmp_git_repo, baseline_sha)

        (tmp_git_repo / "committed.py").write_text("committed = True\n")
        repo.index.add(["committed.py"])
        repo.index.commit("mid-cycle commit")

        (tmp_git_repo / "pending.py").write_text("pending = True\n")
        repo.index.add(["pending.py"])

    diff = materialize_module._pending_diff(tmp_git_repo)

    assert "pending.py" in diff
    assert "committed.py" not in diff


def test_commit_phase_prompt_excludes_mid_cycle_committed_files(
    tmp_git_repo: Path,
) -> None:
    with GitRepo(tmp_git_repo) as repo:
        baseline_sha = repo.head.commit.hexsha
        write_cycle_baseline(tmp_git_repo, baseline_sha)

        (tmp_git_repo / "already_committed.py").write_text("x = 1\n")
        repo.index.add(["already_committed.py"])
        repo.index.commit("earlier dev commit")

        (tmp_git_repo / "new_pending.py").write_text("y = 2\n")
        repo.index.add(["new_pending.py"])

    policy = load_policy(tmp_git_repo / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_git_repo))
    prompt_path = materialize_prompt_for_phase(
        phase="development_commit",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.COMMIT),
        workspace_root=tmp_git_repo,
    )

    rendered = workspace.read(prompt_path)
    assert "new_pending.py" in rendered
    assert "already_committed.py" not in rendered


def test_pending_diff_falls_back_when_not_a_git_repo(tmp_path: Path) -> None:
    diff = materialize_module._pending_diff(tmp_path)
    assert diff == "(no diff available)"


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
        "_pending_diff",
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
        "_pending_diff",
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


def test_development_analysis_prompt_renders_without_development_result(
    tmp_path: Path,
) -> None:
    """development_analysis prompt must render even when development_result.json is absent.

    Since development_result is optional, the analysis agent must still receive
    a complete prompt referencing the plan handoff and diff context.
    LATEST_ARTIFACT may be empty, but prompt generation must not crash.
    """
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "implement the feature")
    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": "ctx",
                        "scope_items": [
                            {"text": "item one"},
                            {"text": "item two"},
                            {"text": "item three"},
                        ],
                    },
                    "steps": [{"number": 1, "title": "step", "content": "do it"}],
                    "critical_files": {"primary_files": [{"path": "src/a.py", "action": "modify"}]},
                    "risks_mitigations": [{"risk": "r", "mitigation": "m"}],
                    "verification_strategy": [{"method": "run tests", "expected_outcome": "pass"}],
                    "work_units": [
                        {"unit_id": "u1", "description": "do stuff", "allowed_directories": ["src"]}
                    ],
                },
            }
        ),
    )
    # Intentionally do NOT write development_result.json

    with patch.object(materialize_module, "_git_diff", return_value="diff --git a/x.py"):
        prompt_path = materialize_prompt_for_phase(
            phase="development_analysis",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            artifacts_policy=policy.artifacts,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        )

    rendered = workspace.read(prompt_path)
    assert rendered, "Prompt must not be empty"
    # render_payload_path emits a file reference, not inlined content — check the path appears
    assert str(tmp_path / ".agent" / "CURRENT_PROMPT.md") in rendered, (
        "Prompt must reference the CURRENT_PROMPT path"
    )
    # Plan is referenced via its Markdown handoff (.agent/PLAN.md), not the JSON artifact path
    assert str(tmp_path / ".agent" / "PLAN.md") in rendered, (
        "Prompt must reference the plan handoff path"
    )


def test_fresh_planning_clears_history_when_clear_on_fresh_entry_enabled(
    tmp_path: Path,
) -> None:
    """Fresh planning entry clears artifact history when planning policy enables it."""
    from ralph.mcp.artifacts.history import (
        history_dir_for_artifact,
        history_index_path,
    )

    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Plan the new feature")

    # Create history files on disk (bypass MemoryWorkspace)
    artifact_dir = tmp_path / ".agent" / "artifacts"
    hist_dir = history_dir_for_artifact(artifact_dir, "plan")
    hist_dir.mkdir(parents=True, exist_ok=True)
    archived_json = hist_dir / "20260506T120000_plan.json"
    archived_json.write_text('{"type":"plan"}', encoding="utf-8")
    index_file = history_index_path(artifact_dir, "plan")
    index_file.write_text("# History", encoding="utf-8")

    materialize_prompt_for_phase(
        phase="planning",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
        workspace_root=tmp_path,
        previous_phase=None,
    )

    assert not archived_json.exists(), "archive json must be removed on fresh planning entry"
    assert not index_file.exists(), "history index must be removed on fresh planning entry"


def test_resolve_planning_history_path_returns_empty_when_no_index(tmp_path: Path) -> None:
    """Returns empty string when no history index exists."""
    from ralph.prompts.materialize import _resolve_planning_history_path

    result = _resolve_planning_history_path(tmp_path)
    assert result == ""


def test_resolve_planning_history_path_returns_path_when_index_exists(tmp_path: Path) -> None:
    """Returns the index path string when the history index file exists."""
    from ralph.mcp.artifacts.history import history_index_path
    from ralph.prompts.materialize import _resolve_planning_history_path

    artifact_dir = tmp_path / ".agent" / "artifacts"
    index = history_index_path(artifact_dir, "plan")
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("# History", encoding="utf-8")

    result = _resolve_planning_history_path(tmp_path)
    assert result == str(index)


def test_planning_loopback_from_analysis_preserves_history(
    tmp_path: Path,
) -> None:
    """Planning loopback from planning_analysis must not clear artifact history."""
    from ralph.mcp.artifacts.history import (
        history_dir_for_artifact,
        history_index_path,
    )

    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Plan the new feature")

    # Write plan + analysis feedback so the loopback prompt can render
    plan_artifact = {
        "type": "plan",
        "content": {
            "summary": {
                "context": "ctx",
                "scope_items": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
            },
            "steps": [
                {
                    "number": 1,
                    "title": "t",
                    "content": "c",
                    "step_type": "file_change",
                    "priority": "high",
                    "targets": [{"path": "f.py", "action": "modify"}],
                    "depends_on": [],
                }
            ],
            "critical_files": {
                "primary_files": [{"path": "f.py", "action": "modify"}],
                "reference_files": [],
            },
            "risks_mitigations": [{"risk": "r", "mitigation": "m", "severity": "low"}],
            "verification_strategy": [{"method": "make test", "expected_outcome": "green"}],
        },
    }
    analysis_artifact = {
        "type": "planning_analysis_decision",
        "content": {
            "status": "request_changes",
            "summary": "Revise the plan.",
            "what_came_up_short": ["Verification is weak."],
            "how_to_fix": ["Add exact commands."],
        },
    }
    import json

    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps(plan_artifact),
    )
    workspace.write(
        ".agent/artifacts/planning_analysis_decision.json",
        json.dumps(analysis_artifact),
    )

    # Create history files on disk (bypass MemoryWorkspace)
    artifact_dir = tmp_path / ".agent" / "artifacts"
    hist_dir = history_dir_for_artifact(artifact_dir, "plan")
    hist_dir.mkdir(parents=True, exist_ok=True)
    archived_json = hist_dir / "20260506T120000_plan.json"
    archived_json.write_text('{"type":"plan"}', encoding="utf-8")
    index_file = history_index_path(artifact_dir, "plan")
    index_file.write_text("# History", encoding="utf-8")

    with patch.object(materialize_module, "_git_diff", return_value="diff"):
        materialize_prompt_for_phase(
            phase="planning",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            artifacts_policy=policy.artifacts,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
            workspace_root=tmp_path,
            previous_phase="planning_analysis",
        )

    assert archived_json.exists(), "archive json must be preserved on planning loopback"
    assert index_file.exists(), "history index must be preserved on planning loopback"


def test_missing_history_does_not_break_fresh_planning(
    tmp_path: Path,
) -> None:
    """Fresh planning entry with no prior history directory must not raise."""
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Plan the new feature")

    # No history files exist at all — history directory does not exist
    artifact_dir = tmp_path / ".agent" / "artifacts"
    assert not (artifact_dir / "history").exists()

    materialize_prompt_for_phase(
        phase="planning",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
        workspace_root=tmp_path,
        previous_phase=None,
    )
    # Must complete without error; no history is also fine
    assert not (artifact_dir / "history").exists()


# ---------------------------------------------------------------------------
# Multimodal sidecar contract tests
# ---------------------------------------------------------------------------


def _make_sidecar_entry(  # noqa: PLR0913
    *,
    artifact_id: str = "abc123",
    uri: str = "ralph://media/abc123",
    mime_type: str = "image/png",
    title: str = "screenshot.png",
    modality: str = "image",
    delivery: str = "inline_image",
    reason: str = "Claude supports inline image delivery",
    source_path: str = "",
    cache_path: str = "",
    source_uri: str = "",
    block_type: str = "",
) -> MultimodalSidecarEntry:
    return MultimodalSidecarEntry(
        artifact_id=artifact_id,
        uri=uri,
        mime_type=mime_type,
        title=title,
        modality=modality,
        delivery=delivery,
        reason=reason,
        source_path=source_path,
        cache_path=cache_path,
        source_uri=source_uri,
        block_type=block_type,
    )


def test_multimodal_sidecar_path_is_deterministic_from_phase() -> None:
    assert (
        multimodal_sidecar_path("development") == ".agent/tmp/development_multimodal_handoff.json"
    )
    assert multimodal_sidecar_path("planning") == ".agent/tmp/planning_multimodal_handoff.json"
    assert multimodal_sidecar_path("foo/bar") == ".agent/tmp/foo_bar_multimodal_handoff.json"
    assert multimodal_sidecar_path("foo bar") == ".agent/tmp/foo_bar_multimodal_handoff.json"


def test_materialize_with_no_multimodal_entries_does_not_create_sidecar(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Build the feature")
    workspace.write(".agent/PLAN.md", "# Implementation Plan\n\nStep 1.\n")

    materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
        previous_phase=None,
        multimodal_entries=None,
    )

    assert not workspace.exists(multimodal_sidecar_path("development"))


def test_materialize_with_empty_multimodal_entries_does_not_create_sidecar(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Build the feature")
    workspace.write(".agent/PLAN.md", "# Implementation Plan\n\nStep 1.\n")

    materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
        previous_phase=None,
        multimodal_entries=[],
    )

    assert not workspace.exists(multimodal_sidecar_path("development"))


def test_materialize_with_multimodal_entries_creates_sidecar(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Build the feature")
    workspace.write(".agent/PLAN.md", "# Implementation Plan\n\nStep 1.\n")

    entry = _make_sidecar_entry()
    materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
        previous_phase=None,
        multimodal_entries=[entry],
    )

    sidecar_path = multimodal_sidecar_path("development")
    assert workspace.exists(sidecar_path)
    data = json.loads(workspace.read(sidecar_path))
    assert data["schema_version"] == "2"
    assert data["phase"] == "development"
    assert len(data["artifacts"]) == 1
    art = data["artifacts"][0]
    assert art["artifact_id"] == "abc123"
    assert art["uri"] == "ralph://media/abc123"
    assert art["mime_type"] == "image/png"
    assert art["title"] == "screenshot.png"
    assert art["modality"] == "image"
    assert art["delivery"] == "inline_image"
    assert art["reason"] == "Claude supports inline image delivery"
    assert "source_path" in art
    assert "cache_path" in art
    assert "source_uri" in art
    assert "block_type" in art


def test_materialize_sidecar_contains_all_artifacts(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Build the feature")
    workspace.write(".agent/PLAN.md", "# Implementation Plan\n\nStep 1.\n")

    entries = [
        _make_sidecar_entry(
            artifact_id="img1",
            uri="ralph://media/img1",
            modality="image",
            title="screen.png",
        ),
        _make_sidecar_entry(
            artifact_id="pdf1",
            uri="ralph://media/pdf1",
            modality="pdf",
            title="doc.pdf",
            mime_type="application/pdf",
            delivery="resource_reference_replay",
        ),
    ]
    materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
        previous_phase=None,
        multimodal_entries=entries,
    )

    data = json.loads(workspace.read(multimodal_sidecar_path("development")))
    assert len(data["artifacts"]) == 2  # noqa: PLR2004
    assert data["artifacts"][0]["artifact_id"] == "img1"
    assert data["artifacts"][1]["artifact_id"] == "pdf1"


def test_stale_sidecar_is_cleared_on_text_only_run(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Build the feature")
    workspace.write(".agent/PLAN.md", "# Implementation Plan\n\nStep 1.\n")

    # First run: multimodal
    entry = _make_sidecar_entry()
    materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
        previous_phase=None,
        multimodal_entries=[entry],
    )
    sidecar_path = multimodal_sidecar_path("development")
    assert workspace.exists(sidecar_path)

    # Second run: text-only (no entries)
    materialize_prompt_for_phase(
        phase="development",
        workspace=workspace,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
        previous_phase=None,
        multimodal_entries=None,
    )

    assert not workspace.exists(sidecar_path), "Stale sidecar must be removed on text-only run"


def test_v1_sidecar_is_read_with_defaults_for_new_fields(
    tmp_path: Path,
) -> None:
    """v1 sidecars (no source_path/cache_path/source_uri/block_type) must load without error."""
    workspace = MemoryWorkspace(root=str(tmp_path))
    v1_payload = json.dumps(
        {
            "schema_version": "1",
            "phase": "development",
            "artifacts": [
                {
                    "artifact_id": "old-id",
                    "uri": "ralph://media/old-id",
                    "mime_type": "image/png",
                    "title": "old.png",
                    "modality": "image",
                    "delivery": "resource_reference",
                    "reason": "prior run",
                }
            ],
        }
    )
    from ralph.prompts.debug_dump import media_session_path

    workspace.write(media_session_path("development"), v1_payload)

    entries = collect_media_entries_for_phase(workspace, "development")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.artifact_id == "old-id"
    assert entry.delivery == "resource_reference"
    assert entry.source_path == ""
    assert entry.cache_path == ""
    assert entry.source_uri == ""
    assert entry.block_type == ""


def test_v2_sidecar_persists_all_new_fields(
    tmp_path: Path,
) -> None:
    """v2 entries must round-trip all new metadata fields through sidecar."""
    workspace = MemoryWorkspace(root=str(tmp_path))
    v2_payload = json.dumps(
        {
            "schema_version": "2",
            "phase": "development",
            "artifacts": [
                {
                    "artifact_id": "new-id",
                    "uri": "ralph://media/new-id",
                    "mime_type": "application/pdf",
                    "title": "doc.pdf",
                    "modality": "pdf",
                    "delivery": "typed_block",
                    "reason": "Claude typed PDF",
                    "source_path": "reports/doc.pdf",
                    "cache_path": ".agent/tmp/media/doc.pdf",
                    "source_uri": "",
                    "block_type": "pdf",
                }
            ],
        }
    )
    from ralph.prompts.debug_dump import media_session_path

    workspace.write(media_session_path("development"), v2_payload)

    entries = collect_media_entries_for_phase(workspace, "development")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.delivery == "typed_block"
    assert entry.source_path == "reports/doc.pdf"
    assert entry.cache_path == ".agent/tmp/media/doc.pdf"
    assert entry.block_type == "pdf"


def test_materialize_sidecar_preserves_delivery_reason_and_block_type_for_mixed_modalities() -> (
    None
):
    """Sidecar round-trip must preserve delivery, reason, and block_type for all modality classes.

    The managed-runtime path carries these fields from the MCP session index through
    the sidecar so prompt-materialization and invoke-time appendix code can use them
    without re-deriving capability information.
    """
    from ralph.prompts.debug_dump import media_session_path

    workspace = MemoryWorkspace()
    payload = json.dumps(
        {
            "schema_version": "2",
            "phase": "development",
            "artifacts": [
                {
                    "artifact_id": "img-rr",
                    "uri": "ralph://media/img-rr",
                    "mime_type": "image/png",
                    "title": "capture.png",
                    "modality": "image",
                    "delivery": "resource_reference_replay",
                    "reason": "unknown provider — defaulting to resource_reference_replay delivery",
                    "source_path": "",
                    "cache_path": "",
                    "source_uri": "",
                    "block_type": "",
                },
                {
                    "artifact_id": "pdf-tb",
                    "uri": "ralph://media/pdf-tb",
                    "mime_type": "application/pdf",
                    "title": "spec.pdf",
                    "modality": "pdf",
                    "delivery": "typed_block",
                    "reason": "'pdf' delivered as typed block 'pdf' for provider 'claude'",
                    "source_path": "docs/spec.pdf",
                    "cache_path": ".agent/tmp/media/spec.pdf",
                    "source_uri": "",
                    "block_type": "pdf",
                },
                {
                    "artifact_id": "aud-rr",
                    "uri": "ralph://media/aud-rr",
                    "mime_type": "audio/mpeg",
                    "title": "meeting.mp3",
                    "modality": "audio",
                    "delivery": "resource_reference_replay",
                    "reason": "unknown provider — defaulting to resource_reference_replay delivery",
                    "source_path": "audio/meeting.mp3",
                    "cache_path": "",
                    "source_uri": "",
                    "block_type": "",
                },
            ],
        }
    )
    workspace.write(media_session_path("development"), payload)

    entries = collect_media_entries_for_phase(workspace, "development")

    assert len(entries) == 3, f"Expected 3 entries, got {len(entries)}"  # noqa: PLR2004

    by_modality = {e.modality: e for e in entries}

    # Image: resource_reference_replay delivery + empty block_type
    img = by_modality["image"]
    assert img.delivery == "resource_reference_replay"
    assert img.reason != "", "reason must not be empty for image entry"
    assert img.block_type == ""

    # PDF: typed_block delivery + non-empty block_type + source_path preserved
    pdf = by_modality["pdf"]
    assert pdf.delivery == "typed_block"
    assert pdf.block_type == "pdf"
    assert pdf.reason != "", "reason must not be empty for PDF typed_block"
    assert pdf.source_path == "docs/spec.pdf"
    assert pdf.cache_path == ".agent/tmp/media/spec.pdf"

    # Audio: resource_reference_replay delivery preserved
    aud = by_modality["audio"]
    assert aud.delivery == "resource_reference_replay"
    assert aud.modality == "audio"
    assert aud.block_type == ""


# ---------------------------------------------------------------------------
# Capability-profile-derived sidecar tests (Plan Step 4 additions)
# ---------------------------------------------------------------------------


def test_sidecar_entries_built_from_capability_profile_verdicts_preserve_all_metadata() -> None:
    """Capability-profile-derived metadata flows correctly through the media session index.

    When the MCP workspace tool writes a media session index using verdicts from
    resolve_capability_profile, collect_media_entries_for_phase must read those entries
    back with delivery, block_type, reason, and URI all intact.
    This proves the end-to-end data contract from capability detection to runner handoff.
    """
    from ralph.mcp.multimodal.capabilities import (
        MultimodalModelIdentity,
        resolve_capability_profile,
    )
    from ralph.prompts.debug_dump import media_session_path

    # Claude profile: image=inline_image, pdf=typed_block, audio=unsupported
    claude_identity = MultimodalModelIdentity(provider="claude", model_id="claude-3-5-sonnet")
    profile = resolve_capability_profile(claude_identity)

    image_v = profile.verdict_for("image")
    pdf_v = profile.verdict_for("pdf")
    audio_v = profile.verdict_for("audio")

    # Write the media session index in the same format the MCP workspace tool uses.
    # This simulates what _write_media_session_entry produces after a read_media call.
    index_payload = json.dumps(
        {
            "schema_version": "2",
            "phase": "development",
            "artifacts": [
                {
                    "artifact_id": "cap-img-001",
                    "uri": "ralph://media/cap-img-001",
                    "mime_type": "image/png",
                    "title": "cap.png",
                    "modality": "image",
                    "delivery": image_v.delivery.value,
                    "reason": image_v.reason,
                    "source_path": "screens/cap.png",
                    "cache_path": "",
                    "source_uri": "",
                    "block_type": image_v.block_type or "",
                },
                {
                    "artifact_id": "cap-pdf-002",
                    "uri": "ralph://media/cap-pdf-002",
                    "mime_type": "application/pdf",
                    "title": "spec.pdf",
                    "modality": "pdf",
                    "delivery": pdf_v.delivery.value,
                    "reason": pdf_v.reason,
                    "source_path": "docs/spec.pdf",
                    "cache_path": ".agent/tmp/media/spec.pdf",
                    "source_uri": "",
                    "block_type": pdf_v.block_type or "",
                },
                {
                    "artifact_id": "cap-aud-003",
                    "uri": "ralph://media/cap-aud-003",
                    "mime_type": "audio/mpeg",
                    "title": "clip.mp3",
                    "modality": "audio",
                    "delivery": audio_v.delivery.value,
                    "reason": audio_v.reason,
                    "source_path": "",
                    "cache_path": "",
                    "source_uri": "",
                    "block_type": audio_v.block_type or "",
                },
            ],
        }
    )

    workspace = MemoryWorkspace()
    workspace.write(media_session_path("development"), index_payload)

    reloaded = collect_media_entries_for_phase(workspace, "development")
    by_modality = {e.modality: e for e in reloaded}
    assert set(by_modality) == {"image", "pdf", "audio"}

    img = by_modality["image"]
    assert img.delivery == "inline_image", f"Image must be inline_image, got {img.delivery!r}"
    assert img.uri == "ralph://media/cap-img-001"
    assert img.reason, "Image entry must carry non-empty reason from capability verdict"

    pdf = by_modality["pdf"]
    assert pdf.delivery == "typed_block", f"PDF must be typed_block, got {pdf.delivery!r}"
    assert pdf.block_type == "pdf", f"PDF block_type must be 'pdf', got {pdf.block_type!r}"
    assert pdf.reason, "PDF entry must carry non-empty reason from capability verdict"
    assert pdf.source_path == "docs/spec.pdf"

    aud = by_modality["audio"]
    assert aud.delivery == "unsupported", (
        f"Audio must be unsupported for Claude, got {aud.delivery!r}"
    )
    assert aud.reason, "Unsupported audio entry must carry non-empty reason"
    assert aud.block_type == "", "Unsupported audio must have empty block_type"


def test_collect_media_entries_preserves_failure_kind_through_sidecar_round_trip() -> None:
    """failure_kind must survive JSON serialization and reload without re-inference.

    Writing a session index entry with failure_kind='unsupported_runtime_seam' and
    reloading via collect_media_entries_for_phase must yield the same value, keeping
    unsupported_runtime_seam distinct from unsupported_modality all the way to invoke time.
    """
    import json

    from ralph.prompts.debug_dump import media_session_path
    from ralph.prompts.materialize import collect_media_entries_for_phase
    from ralph.workspace.memory import MemoryWorkspace

    payload = json.dumps(
        {
            "schema_version": "2",
            "phase": "development",
            "artifacts": [
                {
                    "artifact_id": "seam-fail-001",
                    "uri": "ralph://media/seam-fail-001",
                    "mime_type": "video/mp4",
                    "title": "clip.mp4",
                    "modality": "video",
                    "delivery": "unsupported",
                    "reason": "Active runtime seam cannot carry video through the handoff path",
                    "source_path": "",
                    "cache_path": "",
                    "source_uri": "",
                    "block_type": "",
                    "failure_kind": "unsupported_runtime_seam",
                },
                {
                    "artifact_id": "modality-fail-002",
                    "uri": "ralph://media/modality-fail-002",
                    "mime_type": "audio/mpeg",
                    "title": "clip.mp3",
                    "modality": "audio",
                    "delivery": "unsupported",
                    "reason": "Provider does not support audio",
                    "source_path": "",
                    "cache_path": "",
                    "source_uri": "",
                    "block_type": "",
                    "failure_kind": "unsupported_modality",
                },
            ],
        }
    )

    workspace = MemoryWorkspace()
    workspace.write(media_session_path("development"), payload)

    entries = collect_media_entries_for_phase(workspace, "development")

    assert len(entries) == 2  # noqa: PLR2004
    by_modality = {e.modality: e for e in entries}

    video_e = by_modality["video"]
    assert video_e.failure_kind == "unsupported_runtime_seam", (
        f"failure_kind must survive sidecar round-trip, got: {video_e.failure_kind!r}"
    )
    assert video_e.delivery == "unsupported"

    audio_e = by_modality["audio"]
    assert audio_e.failure_kind == "unsupported_modality", (
        "unsupported_modality failure_kind must survive sidecar round-trip, "
        f"got: {audio_e.failure_kind!r}"
    )
    assert audio_e.delivery == "unsupported"



def test_collect_media_entries_dedupes_repeated_identity_key() -> None:
    import json

    from ralph.prompts.debug_dump import media_session_path
    from ralph.workspace.memory import MemoryWorkspace

    workspace = MemoryWorkspace()
    workspace.write(
        media_session_path("development"),
        json.dumps(
            {
                "schema_version": "2",
                "phase": "development",
                "artifacts": [
                    {
                        "artifact_id": "old-001",
                        "uri": "ralph://media/old-001",
                        "mime_type": "application/pdf",
                        "title": "report.pdf",
                        "modality": "pdf",
                        "delivery": "resource_reference_replay",
                        "reason": "first",
                        "source_path": "docs/report.pdf",
                        "cache_path": ".agent/tmp/media/old-001",
                        "source_uri": "",
                        "block_type": "",
                        "failure_kind": "",
                        "identity_key": "source-path:pdf:docs/report.pdf",
                    },
                    {
                        "artifact_id": "new-002",
                        "uri": "ralph://media/new-002",
                        "mime_type": "application/pdf",
                        "title": "report.pdf",
                        "modality": "pdf",
                        "delivery": "resource_reference_replay",
                        "reason": "second",
                        "source_path": "docs/report.pdf",
                        "cache_path": ".agent/tmp/media/new-002",
                        "source_uri": "",
                        "block_type": "",
                        "failure_kind": "",
                        "identity_key": "source-path:pdf:docs/report.pdf",
                    },
                ],
            }
        ),
    )

    entries = collect_media_entries_for_phase(workspace, "development")

    assert len(entries) == 1
    assert entries[0].artifact_id == "new-002"
    assert entries[0].identity_key == "source-path:pdf:docs/report.pdf"
