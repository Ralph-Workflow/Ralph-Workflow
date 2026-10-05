"""Tests for development_result proof validation in execution phases.

The development phase no longer keys proof validation on plan shape
(step IDs / work-unit IDs) — see U-3. The plan-shape coverage was
removed because the development-analysis feedback loop (U-2) is now
the place where the plan's intent is followed. The surviving
``require_analysis_proof`` field still rejects duplicates, unknown
IDs, and missing analysis findings when analysis feedback exists.
"""

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
Verify: pytest -q
Expect: the repository test suite passes with exit code 0

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
- [DA-001] Plan-level: Criterion: edge-case coverage exists. Expected observation: the focused test covers the edge case. Verdict: not met. Evidence: no matching test. Location: tests/test_main.py. Remaining work: add the missing edge-case test.

## Criterion Verdicts
- [DA-001] Criterion: edge-case coverage exists. Expected observation: the focused test covers the edge case. Verdict: not met. Evidence: no matching test. Location: tests/test_main.py.
""",
    )


def _write_noop_plan(workspace: MemoryWorkspace) -> None:
    workspace.write(
        ".agent/artifacts/plan.md",
        "---\ntype: plan\nnoop: true\n---\nThis explicit no-op plan has enough readable explanatory words for acceptance.\n",
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

Type: discovery
Location: src/example.py
"""
        )
    workspace.write(
        ".agent/artifacts/plan.md",
        "---\ntype: plan\n---\n" + "\n".join(sections),
    )


def _write_subplan_plan(workspace: MemoryWorkspace) -> None:
    workspace.write(
        ".agent/artifacts/plan.md",
        """---
type: plan
---

## API Subplan

### [S-1] Add the API
Implement the API.

Type: file_change
Files:
- modify src/api/main.py
Verify: pytest tests/api -q
Expect: the API tests pass with exit code 0

### [S-2] Test the API
Cover the API behavior.

Type: file_change
Files:
- modify src/api/test_main.py
Verify: pytest tests/api -q
Expect: the API tests pass with exit code 0

## UI Subplan

### [S-3] Add the UI
Implement the UI.

Type: file_change
Files:
- modify src/ui/main.py
Verify: pytest tests/ui -q
Expect: the UI tests pass with exit code 0
""",
    )


def _write_dev_result(
    workspace: MemoryWorkspace,
    *,
    plan_items: object = None,
    analysis_items: object = None,
    artifact_path: str = ".agent/artifacts/development_result.md",
) -> None:
    plan_entries = "\n".join(
        "\n".join(
            (
                f"- [{item['plan_item']}] {item['proof']}",
                f"  Disposition: {item.get('disposition', 'completed')}",
                *((f"  Rationale: {item['rationale']}",) if item.get("rationale") else ()),
            )
        )
        for item in (plan_items or [])
    )
    analysis_entries = "\n".join(
        f"- [{item['how_to_fix_item']}] {item['proof']}" for item in (analysis_items or [])
    )
    workspace.write(
        artifact_path,
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


def test_partial_development_result_skips_proof_validation() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_analysis_feedback(workspace)
    workspace.write(
        ".agent/artifacts/development_result.md",
        """---
type: development_result
status: partial
---
## Summary
- [SUM-1] Ran out of budget mid-refactor; nothing below follows the completed grammar.
""",
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    assert events == [ExecutionResultEvent(phase="development", status="partial")]


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


def test_analysis_proof_policy_can_be_disabled_explicitly(tmp_path: Path) -> None:
    """The only surviving artifact_proof_policy field is ``require_analysis_proof``.

    U-3 removed ``require_plan_proof``; the development phase no longer
    keys proof validation on plan shape. Disabling
    ``require_analysis_proof`` keeps a result that omits a known analysis
    finding from failing proof validation; the plan-shape proof is gone.
    """
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    default_pipeline = (
        Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults" / "pipeline.toml"
    )
    agent_dir.joinpath("pipeline.toml").write_text(
        default_pipeline.read_text(encoding="utf-8")
        .replace("require_analysis_proof = true", "require_analysis_proof = false"),
        encoding="utf-8",
    )
    policy = load_policy(agent_dir)
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_analysis_feedback(workspace)
    _write_dev_result(workspace)  # no analysis_items_addressed, no matching step ID
    ctx = _make_context(workspace, policy=policy)

    events = handle_execution_phase(_invoke(), ctx)

    assert events == [ExecutionResultEvent(phase="development", status="completed")]


def test_planning_phase_keeps_accepted_prose_active() -> None:
    workspace = MemoryWorkspace()
    workspace.write(
        ".agent/artifacts/plan.md",
        "Implement the requested behavior and demonstrate correctness with focused tests and full verification.",
    )
    events = handle_execution_phase(
        InvokeAgentEffect(agent_name="planner", phase="planning", prompt_file="plan.txt"),
        _make_context(workspace),
    )
    assert events == [PipelineEvent.AGENT_SUCCESS]


def test_prose_plan_accepts_any_plan_item_id_without_matching_a_step() -> None:
    """A development_result is accepted with any bracketed ID under a prose plan.

    The pre-U-3 gate required an exact ``[plan]`` proof entry for an
    accepted prose plan; the developer is now free to use any stable
    reference the work actually has (a prose heading, a section anchor,
    or the canonical ``plan`` ID), and the validator never rejects an ID
    solely for not matching a plan-parsed reference.
    """
    workspace = MemoryWorkspace()
    workspace.write(
        ".agent/artifacts/plan.md",
        "Implement the requested behavior and demonstrate correctness with focused tests and full verification.",
    )
    for plan_item in ("plan", "plan-overview", "prose-section-overview"):
        result_workspace = MemoryWorkspace()
        result_workspace.write(
            ".agent/artifacts/plan.md",
            "Implement the requested behavior and demonstrate correctness with focused tests and full verification.",
        )
        _write_dev_result(
            result_workspace,
            plan_items=[{"plan_item": plan_item, "proof": "Verified requested behavior."}],
        )
        events = handle_execution_phase(_invoke(), _make_context(result_workspace))
        assert events == [ExecutionResultEvent(phase="development", status="completed")], plan_item


def test_steps_plan_accepts_any_plan_item_id() -> None:
    """A development_result is accepted for any bracketed ID under a steps plan.

    Pre-U-3 this test asserted ``PROOF INCOMPLETE``; the development
    phase no longer keys proof validation on plan shape, so the result
    is accepted regardless of the bracket ID the developer chose.
    """
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "free-form-section", "proof": "Implemented and verified."}],
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    assert events == [ExecutionResultEvent(phase="development", status="completed")]


def test_plan_proof_rejects_duplicate_plan_item_entries() -> None:
    """Duplicate proof IDs are still rejected — they remain a structural fault.

    The structural duplication check predates U-3 and still rejects two
    plan_items_proven bullets that share the same bracketed ID.
    """
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


def test_nested_criterion_does_not_create_a_global_step_proof_obligation() -> None:
    """A work-unit plan is accepted with a single proof entry keyed to a unit ID.

    The pre-U-3 test asserted the result is accepted because nested
    criteria do not create a global step proof obligation. With U-3, the
    validator no longer reads plan shape at all, so the result is
    accepted for the same reason and any other shape too.
    """
    workspace = MemoryWorkspace()
    _write_nested_work_unit_plan(workspace)
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "api", "proof": "Implemented and proved the API."}],
    )

    events = handle_execution_phase(_invoke(), _make_context(workspace))

    assert events == [ExecutionResultEvent(phase="development", status="completed")]


def test_work_unit_plan_accepts_any_proof_entry() -> None:
    """A work-unit plan is accepted for any single proof entry.

    The pre-U-3 test asserted the result is accepted for the canonical
    step IDs the plan declares. With U-3, any bracketed ID is fine, and
    the test re-models the same plan under a free-form reference to
    prove the validator no longer keys on plan shape.
    """
    workspace = MemoryWorkspace()
    _write_nested_work_unit_plan(workspace)
    _write_dev_result(
        workspace,
        plan_items=[
            {"plan_item": "free-form-overview", "proof": "All work units completed."}
        ],
    )

    events = handle_execution_phase(_invoke(), _make_context(workspace))

    assert events == [ExecutionResultEvent(phase="development", status="completed")]


def test_isolated_worker_accepts_any_proof_entry() -> None:
    """An isolated worker result is accepted for any bracketed ID.

    Pre-U-3 the worker had to cite the assigned unit ID and only that
    ID. The proof-shape constraint is gone: the worker is still bound by
    ``output_artifact_path`` and the analysis-finding coverage check,
    but the plan-shape proof check no longer runs.
    """
    workspace = MemoryWorkspace()
    _write_nested_work_unit_plan(workspace)
    worker_artifact_path = ".agent/workers/api/artifacts/development_result.md"
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "any-free-form-id", "proof": "Completed the API work."}],
        artifact_path=worker_artifact_path,
    )

    events = handle_execution_phase(
        _invoke(),
        _make_context(workspace),
        output_artifact_path=worker_artifact_path,
        assigned_work_unit_id="api",
    )

    assert events == [ExecutionResultEvent(phase="development", status="completed")]


def test_subplan_plan_accepts_any_proof_entry() -> None:
    """A subplan plan is accepted for any bracketed ID.

    Pre-U-3 the test asserted the result was rejected for not covering
    every subplan step. With U-3, the proof-shape check is gone, and
    the result is accepted regardless of the bracket ID.
    """
    workspace = MemoryWorkspace()
    _write_subplan_plan(workspace)
    _write_dev_result(
        workspace,
        plan_items=[
            {"plan_item": "subplan-overview", "proof": "All subplans completed."},
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


def test_analysis_feedback_requires_stable_finding_id() -> None:
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
    assert "analysis finding ID" in failure_events[0].reason


def test_analysis_feedback_rejects_duplicate_finding_entries() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_analysis_feedback(workspace)
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "S-1", "proof": "Implemented."}],
        analysis_items=[
            {"how_to_fix_item": "DA-001", "proof": "Added test 1."},
            {"how_to_fix_item": "DA-001", "proof": "Added test 2."},
        ],
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    failure_events = [event for event in events if isinstance(event, PhaseFailureEvent)]
    assert failure_events
    assert "duplicate" in failure_events[0].reason.lower()


def test_analysis_feedback_rejects_wrong_finding_id_even_when_counts_match() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_analysis_feedback(workspace)
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "S-1", "proof": "Implemented."}],
        analysis_items=[{"how_to_fix_item": "DA-099", "proof": "Evidence"}],
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    failure_events = [event for event in events if isinstance(event, PhaseFailureEvent)]
    assert failure_events
    assert "PROOF INVALID" in failure_events[0].reason
    assert "Unknown analysis finding ID" in failure_events[0].reason


def test_analysis_feedback_passes_with_exact_finding_id() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_analysis_feedback(workspace)
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "S-1", "proof": "Implemented."}],
        analysis_items=[
            {"how_to_fix_item": "DA-001", "proof": "Added test."},
        ],
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    assert events == [ExecutionResultEvent(phase="development", status="completed")]


def test_analysis_feedback_passes_with_any_plan_item_id_and_exact_finding_id() -> None:
    """Plan shape is gone; only the analysis-finding ID is enforced.

    Combines the U-3 contract: any bracket in ``plan_items_proven`` is
    fine, but ``analysis_items_addressed`` must still cover the prior
    ``What Came Up Short`` finding with its exact stable ID.
    """
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_analysis_feedback(workspace)
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "free-form-overview", "proof": "Implemented."}],
        analysis_items=[
            {"how_to_fix_item": "DA-001", "proof": "Added the missing test."},
        ],
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    assert events == [ExecutionResultEvent(phase="development", status="completed")]


def test_proof_failure_preserves_same_session_via_recovery_controller() -> None:
    """The recovery-controller flow still uses an analysis-feedback failure."""
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    _write_analysis_feedback(workspace)
    _write_dev_result(workspace)  # missing the analysis finding entirely
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
