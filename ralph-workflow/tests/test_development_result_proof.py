"""Tests for development_result proof validation in execution phases."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ralph.phases import PhaseContext
from ralph.phases.execution import handle_execution_phase
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.classifier import FailureCategory
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


_FUZZY_ANALYSIS_HOW_TO_FIX = (
    "Edit `ralph-workflow/Makefile` to remove the contradictory "
    "`'(part of verify)'` claim from the doc"
)


@lru_cache(maxsize=1)
def _default_policy_bundle() -> PolicyBundle:
    return load_policy(Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults")


def _make_context(workspace: MemoryWorkspace, policy: PolicyBundle | None = None) -> PhaseContext:
    if policy is None:
        policy = _default_policy_bundle()
    registry: Any = object()
    chain_manager: Any = object()
    agents_policy: Any = object()
    return PhaseContext.construct(
        workspace=workspace,
        registry=registry,
        chain_manager=chain_manager,
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        agents_policy=agents_policy,
        console=None,
    )


def _invoke() -> InvokeAgentEffect:
    return InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.txt")


def _write_plan_steps(workspace: MemoryWorkspace) -> None:
    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": "Test context",
                        "scope_items": [
                            {"text": "Scope item one"},
                            {"text": "Scope item two"},
                            {"text": "Scope item three"},
                        ],
                    },
                    "steps": [
                        {"number": 1, "title": "Add validation", "content": "Do the work"},
                    ],
                    "critical_files": {
                        "primary_files": [{"path": "src/main.py", "action": "modify"}],
                    },
                    "risks_mitigations": [{"risk": "Test risk", "mitigation": "Test mitigation"}],
                    "verification_strategy": [
                        {"method": "Run tests", "expected_outcome": "Tests pass"},
                    ],
                },
            }
        ),
    )


def _write_plan_work_units(workspace: MemoryWorkspace) -> None:
    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps(
            {
                "work_units": [
                    {
                        "unit_id": "u1",
                        "description": "Implement feature",
                        "allowed_directories": ["src"],
                    },
                    {
                        "unit_id": "u2",
                        "description": "Add tests",
                        "allowed_directories": ["tests"],
                    },
                ]
            }
        ),
    )


def _write_analysis_feedback(workspace: MemoryWorkspace) -> None:
    workspace.write(
        ".agent/artifacts/development_analysis_decision.json",
        json.dumps(
            {
                "status": "request_changes",
                "summary": "Issues found",
                "what_came_up_short": ["Missing test"],
                "how_to_fix": ["Add test for edge case"],
            }
        ),
    )


def _write_noop_plan(workspace: MemoryWorkspace) -> None:
    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps({"type": "plan", "content": {"steps": [], "work_units": []}}),
    )


def _write_dev_result(
    workspace: MemoryWorkspace, *, plan_items: object = None, analysis_items: object = None
) -> None:
    workspace.write(
        ".agent/artifacts/development_result.json",
        json.dumps(
            {
                "type": "development_result",
                "content": {
                    "status": "completed",
                    "summary": "Done.",
                    "files_changed": "- src/main.py",
                    "plan_items_proven": plan_items or [],
                    "analysis_items_addressed": analysis_items or [],
                },
            }
        ),
    )


def test_schema_invalid_development_result_returns_phase_failure() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    workspace.write(
        ".agent/artifacts/development_result.json",
        json.dumps({"type": "development_result", "content": {"status": "completed"}}),
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    failure_events = [event for event in events if isinstance(event, PhaseFailureEvent)]
    assert failure_events
    assert failure_events[0].recoverable is True
    assert failure_events[0].failure_category == FailureCategory.ARTIFACT_VALIDATION


def test_proof_policy_can_be_disabled_explicitly(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    default_pipeline = (
        Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults" / "pipeline.toml"
    )
    agent_dir.joinpath("pipeline.toml").write_text(
        default_pipeline.read_text(encoding="utf-8")
        .replace("require_plan_proof = true", "require_plan_proof = false")
        .replace("require_analysis_proof = true", "require_analysis_proof = false"),
        encoding="utf-8",
    )
    policy = load_policy(agent_dir)
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_dev_result(workspace)
    ctx = _make_context(workspace, policy=policy)

    events = handle_execution_phase(_invoke(), ctx)

    assert events == [PipelineEvent.AGENT_SUCCESS]


def test_steps_plan_fails_when_no_proof_is_submitted() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_dev_result(workspace)
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    failure_events = [event for event in events if isinstance(event, PhaseFailureEvent)]
    assert failure_events
    assert failure_events[0].failure_category == FailureCategory.ARTIFACT_VALIDATION
    assert "PROOF INCOMPLETE" in failure_events[0].reason
    hint = workspace.read(".agent/tmp/last_retry_error_development.txt")
    assert hint.splitlines()[0] == "ERROR RECOVERY REQUIRED"
    assert "PREVIOUS ATTEMPT FAILED: proof entries are incomplete or invalid" in hint


def test_proof_failure_preserves_same_session_via_recovery_controller() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_dev_result(workspace)
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)
    failure_event = next(event for event in events if isinstance(event, PhaseFailureEvent))

    state = PipelineState(
        phase="development",
        phase_chains={"development": AgentChainState(agents=["dev"], current_index=0, retries=0)},
        last_agent_session_id="sess-proof-123",
    )
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))

    new_state, _ = reducer_reduce(state, failure_event, recovery=controller)

    assert new_state.session_preserve_retry_pending is True
    assert new_state.last_agent_session_id == "sess-proof-123"
    assert new_state.last_failure_category == FailureCategory.ARTIFACT_VALIDATION


def test_steps_plan_rejects_duplicate_plan_item_entries() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_dev_result(
        workspace,
        plan_items=[
            {"plan_item": "Step 1: Add validation", "proof": "Evidence 1"},
            {"plan_item": "Step 1: Add validation", "proof": "Evidence 2"},
        ],
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    failure_events = [event for event in events if isinstance(event, PhaseFailureEvent)]
    assert failure_events
    assert "Duplicate" in failure_events[0].reason


def test_steps_plan_rejects_wrong_step_title_even_when_counts_match() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "Step 1: Wrong title", "proof": "Implemented."}],
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    failure_events = [event for event in events if isinstance(event, PhaseFailureEvent)]
    assert failure_events
    assert "PROOF INVALID" in failure_events[0].reason
    assert "Unknown plan_item reference" in failure_events[0].reason


def test_work_units_plan_fails_when_no_proof_is_submitted() -> None:
    workspace = MemoryWorkspace()
    _write_plan_work_units(workspace)
    _write_dev_result(workspace)
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    failure_events = [event for event in events if isinstance(event, PhaseFailureEvent)]
    assert failure_events
    assert "at least one work unit" in failure_events[0].reason


def test_work_units_plan_accepts_assigned_unit_id() -> None:
    workspace = MemoryWorkspace()
    _write_plan_work_units(workspace)
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "u1", "proof": "Implemented."}],
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    assert events == [PipelineEvent.AGENT_SUCCESS]


def test_work_units_plan_rejects_unknown_unit_id() -> None:
    workspace = MemoryWorkspace()
    _write_plan_work_units(workspace)
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "unknown", "proof": "Implemented."}],
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    failure_events = [event for event in events if isinstance(event, PhaseFailureEvent)]
    assert failure_events
    assert "Unknown plan_item reference" in failure_events[0].reason


def test_noop_plan_skips_proof_validation() -> None:
    workspace = MemoryWorkspace()
    _write_noop_plan(workspace)
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    assert events == [PipelineEvent.AGENT_SUCCESS]


def test_analysis_feedback_requires_exact_how_to_fix_text() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_analysis_feedback(workspace)
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "Step 1: Add validation", "proof": "Implemented."}],
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    failure_events = [event for event in events if isinstance(event, PhaseFailureEvent)]
    assert failure_events
    assert "how_to_fix item" in failure_events[0].reason


def test_analysis_feedback_rejects_duplicate_how_to_fix_entries() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_analysis_feedback(workspace)
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "Step 1: Add validation", "proof": "Implemented."}],
        analysis_items=[
            {"how_to_fix_item": "Add test for edge case", "proof": "Added test 1."},
            {"how_to_fix_item": "Add test for edge case", "proof": "Added test 2."},
        ],
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    failure_events = [event for event in events if isinstance(event, PhaseFailureEvent)]
    assert failure_events
    assert "Duplicate" in failure_events[0].reason


def test_analysis_feedback_rejects_wrong_item_text_even_when_counts_match() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_analysis_feedback(workspace)
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "Step 1: Add validation", "proof": "Implemented."}],
        analysis_items=[{"how_to_fix_item": "Different text entirely", "proof": "Evidence"}],
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    failure_events = [event for event in events if isinstance(event, PhaseFailureEvent)]
    assert failure_events
    assert "PROOF INVALID" in failure_events[0].reason
    assert "Unknown how_to_fix_item reference" in failure_events[0].reason


def test_analysis_feedback_passes_with_exact_text() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_analysis_feedback(workspace)
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "Step 1: Add validation", "proof": "Implemented."}],
        analysis_items=[
            {"how_to_fix_item": "Add test for edge case", "proof": "Added test."},
        ],
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    assert events == [PipelineEvent.AGENT_SUCCESS]



def test_analysis_feedback_passes_with_case_and_punctuation_variation() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_analysis_feedback(workspace)
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "Step 1: Add validation", "proof": "Implemented."}],
        analysis_items=[
            {
                "how_to_fix_item": (
                    "edit makefile to remove the contradictory part of verify claim "
                    "from the doc"
                ),
                "proof": "Updated the document wording.",
            },
        ],
    )
    workspace.write(
        ".agent/artifacts/development_analysis_decision.json",
        json.dumps(
            {
                "status": "request_changes",
                "summary": "Issues found",
                "what_came_up_short": ["Doc wording was contradictory"],
                "how_to_fix": [_FUZZY_ANALYSIS_HOW_TO_FIX],
            }
        ),
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    assert events == [PipelineEvent.AGENT_SUCCESS]



def test_analysis_feedback_passes_with_minor_spelling_variation() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    workspace.write(
        ".agent/artifacts/development_analysis_decision.json",
        json.dumps(
            {
                "status": "request_changes",
                "summary": "Issues found",
                "what_came_up_short": ["Doc wording was contradictory"],
                "how_to_fix": [_FUZZY_ANALYSIS_HOW_TO_FIX],
            }
        ),
    )
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "Step 1: Add validation", "proof": "Implemented."}],
        analysis_items=[
            {
                "how_to_fix_item": (
                    "Edit ralph workflow Makefile to remove the contradictry part of "
                    "verify claim from the doc"
                ),
                "proof": "Updated the document wording.",
            },
        ],
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    assert events == [PipelineEvent.AGENT_SUCCESS]
