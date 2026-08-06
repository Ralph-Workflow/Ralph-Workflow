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
- [FIX-1] Plan-level: Criterion: edge-case coverage exists. Expected observation: the focused test covers the edge case. Verdict: not met. Evidence: no matching test. Location: tests/test_main.py.
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

Type: discovery
Location: src/example.py
"""
        )
    workspace.write(
        ".agent/artifacts/plan.md",
        "---\ntype: plan\n---\n" + "\n".join(sections),
    )


def _write_work_units_with_main_fan_in(workspace: MemoryWorkspace) -> None:
    workspace.write(
        ".agent/artifacts/plan.md",
        """---
type: plan
---
## Work Units
- [api] Implement the API unit
  Directories: src/api

### [S-1] Implement API
Change the API component.

Type: discovery
Location: src/api/routes.py

## Work Units
- [web] Implement the web unit
  Directories: src/web

### [S-2] Implement web
Change the web component.

Type: discovery
Location: src/web/client.py

## Integration and Verification

### [S-3] Integrate and verify
Integrate both unit results in the main session.

Type: discovery
Location: reports/integration-proof.json
Depends on: S-1, S-2
""",
    )


def _write_work_unit_with_nested_criterion(workspace: MemoryWorkspace) -> None:
    workspace.write(
        ".agent/artifacts/plan.md",
        """---
type: plan
---
## Work Units
- [api] Implement and prove the API unit
  Directories: src/api

### [S-1] Implement API
Change the API component.

Type: discovery
Location: reports/api-proof.json

- [AC-01] The API report proves completion
  Satisfied by: S-1
  Evidence: reports/api-proof.json
""",
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
        f"- [{item['plan_item']}] {item['proof']}" for item in (plan_items or [])
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


def test_main_work_unit_result_rejects_one_of_five_unit_proofs() -> None:
    workspace = MemoryWorkspace()
    _write_nested_work_unit_plan(workspace)
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "api", "proof": "Completed api."}],
    )

    events = handle_execution_phase(_invoke(), _make_context(workspace))

    failure_event = next(event for event in events if isinstance(event, PhaseFailureEvent))
    assert "PROOF INCOMPLETE" in failure_event.reason
    assert "contract" in failure_event.reason
    assert "integration" in failure_event.reason
    assert "web" in failure_event.reason


def test_main_work_unit_result_accepts_all_five_unit_proofs() -> None:
    workspace = MemoryWorkspace()
    _write_nested_work_unit_plan(workspace)
    _write_dev_result(
        workspace,
        plan_items=[
            {"plan_item": unit_id, "proof": f"Completed {unit_id}."}
            for unit_id in ("api", "web", "docs", "contract", "integration")
        ],
    )

    events = handle_execution_phase(_invoke(), _make_context(workspace))

    assert events == [ExecutionResultEvent(phase="development", status="completed")]


def test_nested_criterion_does_not_create_a_global_step_proof_obligation() -> None:
    workspace = MemoryWorkspace()
    _write_work_unit_with_nested_criterion(workspace)
    _write_dev_result(
        workspace,
        plan_items=[{"plan_item": "api", "proof": "Implemented and proved the API."}],
    )

    events = handle_execution_phase(_invoke(), _make_context(workspace))

    assert events == [ExecutionResultEvent(phase="development", status="completed")]


def test_main_work_unit_result_requires_unowned_fan_in_step_proof() -> None:
    workspace = MemoryWorkspace()
    _write_work_units_with_main_fan_in(workspace)
    _write_dev_result(
        workspace,
        plan_items=[
            {"plan_item": "api", "proof": "Completed API work."},
            {"plan_item": "web", "proof": "Completed web work."},
        ],
    )

    events = handle_execution_phase(_invoke(), _make_context(workspace))

    failure_event = next(event for event in events if isinstance(event, PhaseFailureEvent))
    assert "PROOF INCOMPLETE" in failure_event.reason
    assert "S-3" in failure_event.reason


def test_main_work_unit_result_accepts_units_plus_unowned_fan_in_steps() -> None:
    workspace = MemoryWorkspace()
    _write_work_units_with_main_fan_in(workspace)
    _write_dev_result(
        workspace,
        plan_items=[
            {"plan_item": "api", "proof": "Completed API work."},
            {"plan_item": "web", "proof": "Completed web work."},
            {"plan_item": "S-3", "proof": "Integrated both units and ran the final checks."},
        ],
    )

    events = handle_execution_phase(_invoke(), _make_context(workspace))

    assert events == [ExecutionResultEvent(phase="development", status="completed")]


def test_isolated_worker_accepts_exactly_its_assigned_unit_proof() -> None:
    """Each worker proves one assigned unit while the main result proves all units."""
    for unit_id in ("api", "web", "docs", "contract", "integration"):
        workspace = MemoryWorkspace()
        _write_nested_work_unit_plan(workspace)
        worker_artifact_path = f".agent/workers/{unit_id}/artifacts/development_result.md"
        _write_dev_result(
            workspace,
            plan_items=[{"plan_item": unit_id, "proof": f"Completed {unit_id}."}],
            artifact_path=worker_artifact_path,
        )

        events = handle_execution_phase(
            _invoke(),
            _make_context(workspace),
            output_artifact_path=worker_artifact_path,
            assigned_work_unit_id=unit_id,
        )

        assert events == [ExecutionResultEvent(phase="development", status="completed")]


def test_isolated_worker_assignment_is_authoritative_for_linear_plan_proof() -> None:
    workspace = MemoryWorkspace()
    _write_plan_steps(workspace)
    worker_artifact_path = ".agent/workers/runtime-unit/artifacts/development_result.md"
    _write_dev_result(
        workspace,
        plan_items=[
            {
                "plan_item": "runtime-unit",
                "proof": "Completed the runtime-assigned unit.",
            }
        ],
        artifact_path=worker_artifact_path,
    )

    events = handle_execution_phase(
        _invoke(),
        _make_context(workspace),
        output_artifact_path=worker_artifact_path,
        assigned_work_unit_id="runtime-unit",
    )

    assert events == [ExecutionResultEvent(phase="development", status="completed")]


def test_isolated_worker_rejects_an_extra_unit_proof() -> None:
    workspace = MemoryWorkspace()
    _write_nested_work_unit_plan(workspace)
    worker_artifact_path = ".agent/workers/api/artifacts/development_result.md"
    _write_dev_result(
        workspace,
        plan_items=[
            {"plan_item": "api", "proof": "Completed api."},
            {"plan_item": "web", "proof": "Also changed web."},
        ],
        artifact_path=worker_artifact_path,
    )

    events = handle_execution_phase(
        _invoke(),
        _make_context(workspace),
        output_artifact_path=worker_artifact_path,
        assigned_work_unit_id="api",
    )

    failure_event = next(event for event in events if isinstance(event, PhaseFailureEvent))
    assert "exactly one proof" in failure_event.reason
    assert "web" in failure_event.reason
    assert workspace.exists(".agent/workers/api/tmp/last_retry_error_development.txt")
    assert not workspace.exists(".agent/tmp/last_retry_error_development.txt")


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


def test_subplan_main_result_requires_every_step_not_only_synthetic_unit_ids() -> None:
    workspace = MemoryWorkspace()
    _write_subplan_plan(workspace)
    _write_dev_result(
        workspace,
        plan_items=[
            {"plan_item": "S-1", "proof": "Completed API subplan."},
            {"plan_item": "S-3", "proof": "Completed UI subplan."},
        ],
    )

    events = handle_execution_phase(_invoke(), _make_context(workspace))

    failure_event = next(event for event in events if isinstance(event, PhaseFailureEvent))
    assert "PROOF INCOMPLETE" in failure_event.reason
    assert "S-2" in failure_event.reason


def test_subplan_main_result_rejects_complete_synthetic_worker_unit_proof() -> None:
    workspace = MemoryWorkspace()
    _write_subplan_plan(workspace)
    _write_dev_result(
        workspace,
        plan_items=[
            {
                "plan_item": "subplan-s-1",
                "proof": "Completed the API subplan.",
            },
            {
                "plan_item": "subplan-s-3",
                "proof": "Completed the UI subplan.",
            },
        ],
    )

    events = handle_execution_phase(_invoke(), _make_context(workspace))

    failure_event = next(event for event in events if isinstance(event, PhaseFailureEvent))
    assert "PROOF INCOMPLETE" in failure_event.reason
    assert "S-1" in failure_event.reason
    assert "S-2" in failure_event.reason
    assert "S-3" in failure_event.reason
    assert "subplan-s-1" in failure_event.reason


def test_subplan_isolated_worker_uses_synthetic_unit_id_not_every_owned_step() -> None:
    workspace = MemoryWorkspace()
    _write_subplan_plan(workspace)
    worker_artifact_path = ".agent/workers/subplan-s-1/artifacts/development_result.md"
    _write_dev_result(
        workspace,
        plan_items=[
            {
                "plan_item": "subplan-s-1",
                "proof": "Completed the API subplan.",
            }
        ],
        artifact_path=worker_artifact_path,
    )

    events = handle_execution_phase(
        _invoke(),
        _make_context(workspace),
        output_artifact_path=worker_artifact_path,
        assigned_work_unit_id="subplan-s-1",
    )

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
            {"how_to_fix_item": "FIX-1", "proof": "Added test 1."},
            {"how_to_fix_item": "FIX-1", "proof": "Added test 2."},
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
        analysis_items=[{"how_to_fix_item": "FIX-99", "proof": "Evidence"}],
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
            {"how_to_fix_item": "FIX-1", "proof": "Added test."},
        ],
    )
    ctx = _make_context(workspace)

    events = handle_execution_phase(_invoke(), ctx)

    assert events == [ExecutionResultEvent(phase="development", status="completed")]
