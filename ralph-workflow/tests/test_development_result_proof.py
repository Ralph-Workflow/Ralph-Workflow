"""Tests for development_result proof validation in execution phases."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ralph.phases import PhaseContext
from ralph.phases.execution import handle_execution_phase
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import ExecutionResultEvent, PhaseFailureEvent, PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.classifier import FailureCategory
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


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
        ".agent/artifacts/plan.md",
        """---
type: plan
schema_version: 1
---
## Summary
Test context.

Intent: Add validation.
Coverage: feature

## Scope
- [SC-1] Add validation
  Category: feature
- [SC-2] Preserve proof validation
  Category: test
- [SC-3] Verify the result
  Category: test

## Skills MCP
Skills: test-driven-development, verification-before-completion

## Steps

### [S-1] Add validation
Do the work.

Type: file_change
Files:
- modify src/main.py

## Critical Files
- [CF-1] src/main.py
  Action: modify
  Changes: add validation

## Risks
- [R-1] Validation regresses
  Severity: medium
  Mitigation: Run the focused test.

## Verification
- [V-1] pytest -q
  Expect: tests pass
""",
    )


def _write_analysis_feedback(workspace: MemoryWorkspace) -> None:
    workspace.write(
        ".agent/artifacts/development_analysis_decision.md",
        """---
type: development_analysis_decision
status: request_changes
---
## Summary
- [SUM-1] Issues found.

## What Came Up Short
- [W-1] Missing test.

## How To Fix
- [FIX-1] Add test for edge case.
""",
    )


def _write_noop_plan(workspace: MemoryWorkspace) -> None:
    workspace.write(
        ".agent/artifacts/plan.md",
        "---\ntype: plan\nnoop: true\n---\n",
    )


def _write_nested_work_unit_plan(workspace: MemoryWorkspace) -> None:
    sections = []
    for number, name in enumerate(("api", "web", "docs", "contract", "integration"), start=1):
        sections.append(
            f"""## Work Units
- [{name}] Implement the {name} unit
  Directories: src/{name}

### [S-{number}] Implement {name}
Change the {name} component.

Type: action
"""
        )
    workspace.write(
        ".agent/artifacts/plan.md",
        "---\ntype: plan\n---\n" + "\n".join(sections),
    )


def _write_dev_result(
    workspace: MemoryWorkspace, *, plan_items: object = None, analysis_items: object = None
) -> None:
    plan_entries = "\n".join(
        f"- [{item['plan_item']}] {item['proof']}" for item in (plan_items or [])
    )
    analysis_entries = "\n".join(
        f"- [{item['how_to_fix_item']}] {item['proof']}" for item in (analysis_items or [])
    )
    workspace.write(
        ".agent/artifacts/development_result.md",
        f"""---
type: development_result
status: completed
---
## Summary
- [SUM-1] Done.

## Files Changed
- [F-1] src/main.py

## Plan Items Proven
{plan_entries}

## Analysis Items Addressed
{analysis_entries}
""",
    )


def test_schema_invalid_development_result_returns_phase_failure() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    workspace.write(
        ".agent/artifacts/development_result.md",
        "---\ntype: development_result\nstatus: completed\n---\n",
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

    assert events == [ExecutionResultEvent(phase="development", status="completed")]


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

    assert new_state.agent_retry_intent.action == "resume"
    assert new_state.agent_retry_intent.session_id == "sess-proof-123"
    assert new_state.last_agent_session_id == "sess-proof-123"
    assert new_state.last_failure_category == FailureCategory.ARTIFACT_VALIDATION
    assert new_state.last_error is not None
    assert "Artifact validation fault" in new_state.last_error


def test_steps_plan_rejects_duplicate_plan_item_entries() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_dev_result(
        workspace,
        plan_items=[
            {"plan_item": "S-1", "proof": "Evidence 1"},
            {"plan_item": "S-1", "proof": "Evidence 2"},
        ],
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    failure_events = [event for event in events if isinstance(event, PhaseFailureEvent)]
    assert failure_events
    assert "duplicate" in failure_events[0].reason.lower()


def test_steps_plan_rejects_wrong_step_title_even_when_counts_match() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "S-99", "proof": "Implemented."}],
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    failure_events = [event for event in events if isinstance(event, PhaseFailureEvent)]
    assert failure_events
    assert "PROOF INVALID" in failure_events[0].reason
    assert "Unknown plan_item reference" in failure_events[0].reason


def test_work_unit_proof_regression_accepts_each_of_five_parallel_worker_ids() -> None:
    """Regression for plan blocker 6: each worker proves its assigned unit, not all steps."""
    for unit_id in ("api", "web", "docs", "contract", "integration"):
        workspace = MemoryWorkspace()
        _write_nested_work_unit_plan(workspace)
        _write_dev_result(
            workspace,
            plan_items=[{"plan_item": unit_id, "proof": f"Completed {unit_id}."}],
        )

        events = handle_execution_phase(_invoke(), _make_context(workspace))

        assert events == [ExecutionResultEvent(phase="development", status="completed")]


def test_work_unit_plan_preserves_complete_global_step_proof_for_serial_execution() -> None:
    """Preservation pin: accepted mixed plans may still prove all global step IDs."""
    workspace = MemoryWorkspace()
    _write_nested_work_unit_plan(workspace)
    _write_dev_result(
        workspace,
        plan_items=[
            {"plan_item": f"S-{number}", "proof": f"Completed step {number}."}
            for number in range(1, 6)
        ],
    )

    events = handle_execution_phase(_invoke(), _make_context(workspace))

    assert events == [ExecutionResultEvent(phase="development", status="completed")]


def test_noop_plan_skips_proof_validation() -> None:
    workspace = MemoryWorkspace()
    _write_noop_plan(workspace)
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    assert events == [PipelineEvent.AGENT_SUCCESS]


def test_analysis_feedback_requires_stable_how_to_fix_id() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_analysis_feedback(workspace)
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "S-1", "proof": "Implemented."}],
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    failure_events = [event for event in events if isinstance(event, PhaseFailureEvent)]
    assert failure_events
    assert "analysis item ID" in failure_events[0].reason


def test_analysis_feedback_rejects_duplicate_how_to_fix_entries() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_analysis_feedback(workspace)
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "S-1", "proof": "Implemented."}],
        analysis_items=[
            {"how_to_fix_item": "FIX-1", "proof": "Added test 1."},
            {"how_to_fix_item": "FIX-1", "proof": "Added test 2."},
        ],
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    failure_events = [event for event in events if isinstance(event, PhaseFailureEvent)]
    assert failure_events
    assert "duplicate" in failure_events[0].reason.lower()


def test_analysis_feedback_rejects_wrong_item_text_even_when_counts_match() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_analysis_feedback(workspace)
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "S-1", "proof": "Implemented."}],
        analysis_items=[{"how_to_fix_item": "FIX-99", "proof": "Evidence"}],
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    failure_events = [event for event in events if isinstance(event, PhaseFailureEvent)]
    assert failure_events
    assert "PROOF INVALID" in failure_events[0].reason
    assert "Unknown how_to_fix_item ID" in failure_events[0].reason


def test_analysis_feedback_passes_with_exact_text() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_analysis_feedback(workspace)
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "S-1", "proof": "Implemented."}],
        analysis_items=[
            {"how_to_fix_item": "FIX-1", "proof": "Added test."},
        ],
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    assert events == [ExecutionResultEvent(phase="development", status="completed")]
