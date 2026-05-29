from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest

from ralph.mcp.artifacts.handoffs import HANDOFF_PATHS
from ralph.mcp.artifacts.history import (
    history_index_path,
)
from ralph.mcp.tools.artifact import (
    handle_finalize_plan,
    handle_get_plan_draft,
    handle_submit_artifact,
    handle_submit_plan_section,
)
from ralph.mcp.tools.tool_content import ToolContent
from ralph.policy.loader import load_policy
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.template_engine import TemplateRenderingError
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace
from tests.test_prompt_materialize_1_helper__artifactworkspace import _ArtifactWorkspace

_TextContent = ToolContent



class _ArtifactSubmitSession:
    session_id = "test-session"
    drain = "planning_analysis"

    def check_capability(self, capability: str) -> object:
        return capability in {"artifact.submit", "artifact.plan_read", "artifact.plan_write"}




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


def test_materialize_prompt_for_phase_renders_planning_prompt_to_agent_tmp(tmp_path: Path) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Plan the template migration")

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
            previous_phase=None,
        ),
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

    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Create a brand new plan")
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
            previous_phase=None,
        ),
    )

    rendered = workspace.read(prompt_path)
    assert "ARTIFACT HISTORY" not in rendered
    assert str(plan_history_file) not in rendered
    assert str(development_history_file) not in rendered
    assert plan_history_file.exists() is False
    assert development_history_file.exists() is False


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
                    "skills_mcp": {
                        "skills": [
                            "test-driven-development",
                            "verification-before-completion",
                        ],
                        "mcps": [],
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
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\nOld plan handoff.\n")
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
        PromptPhaseContext(
            phase="planning",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
            previous_phase=None,
        ),
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
                    "skills_mcp": {
                        "skills": [
                            "test-driven-development",
                            "verification-before-completion",
                        ],
                        "mcps": [],
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
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\nStale handoff.\n")
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
            PromptPhaseContext(
                phase="planning",
                workspace=workspace,
                pipeline_policy=policy.pipeline,
                session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
                workspace_root=tmp_path,
            ),
            PromptPhaseOptions(
                artifacts_policy=policy.artifacts,
                previous_phase=None,
            ),
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


def test_planning_loopback_prompt_and_plan_edit_mcp_contract_stay_consistent(
    tmp_path: Path,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    artifact_workspace = _ArtifactWorkspace(tmp_path)
    session = _ArtifactSubmitSession()
    workspace.write("PROMPT.md", "Refine the plan based on analysis feedback")

    plan_artifact = {
        "summary": {
            "context": "Existing plan to preserve.",
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
        "steps": [
            {
                "number": 1,
                "title": "Keep",
                "content": "keep existing",
                "targets": [{"path": "src/keep.py", "action": "modify"}],
            }
        ],
        "critical_files": {
            "primary_files": [{"path": "src/keep.py", "action": "modify"}],
            "reference_files": [{"path": "PROMPT.md", "purpose": "original request"}],
        },
        "risks_mitigations": [{"risk": "drift", "mitigation": "preserve"}],
        "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
    }
    handle_submit_artifact(
        session,
        artifact_workspace,
        {"artifact_type": "plan", "content": json.dumps(plan_artifact)},
    )
    handle_submit_artifact(
        session,
        artifact_workspace,
        {
            "artifact_type": "planning_analysis_decision",
            "content": json.dumps(
                {
                    "status": "request_changes",
                    "summary": "The plan needs revisions.",
                    "what_came_up_short": ["Verification commands are too vague."],
                    "how_to_fix": ["Edit the existing plan draft instead of restarting."],
                }
            ),
        },
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
    expected_feedback_path = tmp_path / HANDOFF_PATHS["planning_analysis_decision"]
    assert str(expected_feedback_path) in rendered
    assert PLANNING_EDIT_GET_DRAFT_TEXT in rendered
    assert "ralph_submit_plan_section" in rendered
    assert "ralph_finalize_plan" in rendered

    draft_result = handle_get_plan_draft(session, artifact_workspace, {})
    draft_text = cast("_TextContent", draft_result.content[0]).text
    draft_payload = json.loads(draft_text)
    assert draft_payload["source"] == "finalized_plan"

    revised_steps = [
        {
            "number": 1,
            "title": "Review analyzer feedback against the existing plan",
            "content": (
                "Use the analysis artifact to tighten verification and preserve valid scope."
            ),
            "targets": [
                {"path": ".agent/PLANNING_ANALYSIS_DECISION.md", "action": "read"},
                {"path": ".agent/CURRENT_PROMPT.md", "action": "reference"},
                {"path": "PROMPT.md", "action": "reference"},
            ],
        }
    ]
    revised_verification = [
        {
            "method": "uv run pytest -q tests/test_prompt_materialize.py",
            "expected_outcome": "planning edit contract stays aligned with MCP plan revision flow.",
        }
    ]
    handle_submit_plan_section(
        session,
        artifact_workspace,
        {"section": "steps", "content": json.dumps(revised_steps)},
    )
    handle_submit_plan_section(
        session,
        artifact_workspace,
        {"section": "verification_strategy", "content": json.dumps(revised_verification)},
    )
    finalize_result = handle_finalize_plan(session, artifact_workspace, {})
    assert finalize_result.is_error is False

    stored = json.loads(
        (tmp_path / ".agent" / "artifacts" / "plan.json").read_text(encoding="utf-8")
    )
    content = stored["content"]
    assert content["steps"] == revised_steps
    assert content["verification_strategy"] == revised_verification
    assert content["critical_files"]["reference_files"] == [
        {"path": "PROMPT.md", "purpose": "original request"}
    ]


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
                    "skills_mcp": {
                        "skills": [
                            "test-driven-development",
                            "verification-before-completion",
                        ],
                        "mcps": [],
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
    assert "PLANNING MODE" not in rendered
    assert "Your job is to revise the existing plan" in rendered


def test_planning_loopback_prompt_includes_artifact_history_when_history_exists(
    tmp_path: Path,
) -> None:

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
            previous_phase="planning_analysis",
        ),
    )

    rendered = workspace.read(prompt_path)
    assert "ARTIFACT HISTORY" in rendered
    assert str(plan_history_file) in rendered


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
                    "skills_mcp": {
                        "skills": [
                            "test-driven-development",
                            "verification-before-completion",
                        ],
                        "mcps": [],
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
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\nCurrent retryable plan context.\n")
    workspace.write(
        ".agent/tmp/last_retry_error_planning.txt",
        "PREVIOUS ATTEMPT FAILED: validation error during planning retry",
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
            previous_phase="planning",
        ),
    )

    rendered = workspace.read(prompt_path)
    assert "PLANNING EDIT MODE" in rendered
    assert "PREVIOUS ATTEMPT ERROR" in rendered
    assert str(tmp_path / ".agent" / "PLAN.md") not in rendered
    assert "ralph_get_plan_draft" in rendered
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
        PromptPhaseContext(
            phase="planning",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
            previous_phase=None,
            resume_existing_phase=True,
        ),
    )

    rendered = workspace.read(prompt_path)
    assert "PLANNING EDIT MODE" in rendered
    assert "Resumed draft-only context." not in rendered
    assert "ralph_get_plan_draft" in rendered
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
                    "skills_mcp": {
                        "skills": [
                            "test-driven-development",
                            "verification-before-completion",
                        ],
                        "mcps": [],
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
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\nResumed plan context.\n")
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
        PromptPhaseContext(
            phase="planning",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
            previous_phase=None,
            resume_existing_phase=True,
        ),
    )

    rendered = workspace.read(prompt_path)
    assert "PLANNING EDIT MODE" in rendered
    assert workspace.exists(".agent/artifacts/plan.json") is True
    assert workspace.exists(".agent/artifacts/.plan_draft.json") is True
    assert workspace.exists(".agent/PLAN.md") is True
