"""Black-box test: ambiguous failures are classified correctly and flagged for review."""

from __future__ import annotations

import errno
import importlib
import io

import pytest
from loguru import logger

from ralph.config.mcp_loader import McpConfigError
from ralph.mcp.artifacts.development_result_validation_error import (
    DevelopmentResultValidationError,
)
from ralph.mcp.artifacts.plan import (
    PlanArtifactValidationError,
    normalize_plan_artifact_content,
    render_plan_markdown,
)
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.classifier import FailureCategory, FailureClassifier
from ralph.recovery.controller import (
    FailureContext,
    RecoveryController,
    RecoveryControllerOptions,
)


def _make_state(agents: list[str] | None = None) -> PipelineState:
    if agents is None:
        agents = ["claude"]
    return PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=agents, current_index=0, retries=0
            )
        },
    )


def test_unknown_exception_is_ambiguous() -> None:
    """An exception that doesn't match known patterns is classified as AMBIGUOUS."""
    classifier = FailureClassifier()

    failure = classifier.classify(
        RuntimeError("something went wrong but not sure what"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.AMBIGUOUS
    assert failure.counts_against_budget is False


def test_generic_exception_message_ambiguous() -> None:
    """Generic error messages that don't match transport patterns are ambiguous."""
    classifier = FailureClassifier()

    failure = classifier.classify(
        ValueError("invalid input provided"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.AMBIGUOUS
    assert failure.counts_against_budget is False


def test_ambiguous_failure_does_not_debit_budget() -> None:
    """Ambiguous failures must not count against the agent budget."""
    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=3)
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, budget_registry=registry)
    )
    state = _make_state()

    _, _, evt = controller.handle(
        state,
        RuntimeError("something unexpected happened"),
        FailureContext(phase="development", agent="claude"),
    )

    assert evt.counted_against_budget is False
    assert evt.category == "ambiguous"

    # Budget should not be debited
    budget_state = controller.budget_registry.get("development", "claude")
    assert budget_state is not None
    assert budget_state.consumed == 0


def test_ambiguous_failure_returns_state_without_phase_change() -> None:
    """Ambiguous failures keep the pipeline running in the same phase."""
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))
    state = _make_state()

    new_state, effects, evt = controller.handle(
        state,
        OSError("some system error"),
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.phase == "development"
    assert effects == []  # No exit effect
    assert evt.counted_against_budget is False


def test_ambiguous_failure_is_flagged_in_reason() -> None:
    """Ambiguous failure reason includes the flagged_for_review indication."""
    classifier = FailureClassifier()

    failure = classifier.classify(
        Exception("an exception without clear attribution"),
        phase="development",
        agent="claude",
    )

    assert FailureCategory.AMBIGUOUS in failure.reason or "flagged" in failure.reason.lower()


def test_ambiguous_failure_emits_warning_log() -> None:
    """Ambiguous failures emit a warning log flagged for review."""
    sink = io.StringIO()
    handler_id = logger.add(sink, level="WARNING", format="{level} {message}")
    try:
        classifier = FailureClassifier()
        failure = classifier.classify(
            RuntimeError("unrelated failure"),
            phase="development",
            agent="claude",
        )
        assert failure.category == FailureCategory.AMBIGUOUS
        assert failure.counts_against_budget is False
        log_output = sink.getvalue()
        assert (
            "flagged_for_review" in log_output.lower() or "ambiguous" in log_output.lower()
        )
    finally:
        logger.remove(handler_id)


def test_artifact_validation_failure_is_not_flagged_as_ambiguous() -> None:
    sink = io.StringIO()
    handler_id = logger.add(sink, level="WARNING", format="{level} {message}")
    try:
        classifier = FailureClassifier()
        failure = classifier.classify(
            "PROOF INCOMPLETE: The following how_to_fix item(s) have no proof entry: ['Add test']",
            phase="development",
            agent="claude",
        )
        assert failure.category == FailureCategory.ARTIFACT_VALIDATION
        assert "flagged_for_review" not in sink.getvalue().lower()
    finally:
        logger.remove(handler_id)


def test_mcp_config_error_is_user_config_not_ambiguous() -> None:
    classifier = FailureClassifier()

    failure = classifier.classify(
        McpConfigError("fallback backend 'searxng' is not configured"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.USER_CONFIG
    assert failure.counts_against_budget is False


def test_connection_refused_is_not_ambiguous() -> None:
    """ConnectionRefused is clearly environmental, not ambiguous."""
    classifier = FailureClassifier()

    failure = classifier.classify(
        ConnectionRefusedError("connection refused"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.ENVIRONMENTAL
    assert failure.counts_against_budget is False


def test_timeout_error_is_environmental() -> None:
    """TimeoutError is environmental, not ambiguous."""
    classifier = FailureClassifier()

    failure = classifier.classify(
        TimeoutError("operation timed out"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.ENVIRONMENTAL
    assert failure.counts_against_budget is False


def test_agent_inactivity_timeout_is_agent_fault() -> None:
    """AgentInactivityTimeoutError is agent fault, not ambiguous."""
    classifier = FailureClassifier()

    class AgentInactivityTimeoutError(Exception):
        pass

    AgentInactivityTimeoutError.__name__ = "AgentInactivityTimeoutError"

    failure = classifier.classify(
        AgentInactivityTimeoutError("agent idle for too long"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.AGENT
    assert failure.counts_against_budget is True


def test_enospc_oserror_is_environmental_not_ambiguous() -> None:
    """OSError with errno.ENOSPC must classify as ENVIRONMENTAL, not AMBIGUOUS."""
    classifier = FailureClassifier()

    failure = classifier.classify(
        OSError(errno.ENOSPC, "No space left on device"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.ENVIRONMENTAL
    assert failure.counts_against_budget is False


def test_enospc_oserror_via_controller_does_not_debit_budget() -> None:
    """ENOSPC reaching RecoveryController must not debit agent budget."""
    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=3)
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, budget_registry=registry)
    )
    state = _make_state()

    _, _, evt = controller.handle(
        state,
        OSError(errno.ENOSPC, "No space left on device"),
        FailureContext(phase="development", agent="claude"),
    )

    assert evt.category == "environmental"
    assert evt.counted_against_budget is False

    budget_state = controller.budget_registry.get("development", "claude")
    assert budget_state is not None
    assert budget_state.consumed == 0


# ---------------------------------------------------------------------------
# PA-001 / PA-002 regression tests — typed *ValidationError dispatch
# ---------------------------------------------------------------------------


def test_render_plan_markdown_with_prompt_shape_payload_routes_to_artifact_validation() -> (
    None
):
    """Drive the malformed-plan shape from the prompt traceback through the real
    materialize-handoff code.

    The prompt's failure originated at ensure_markdown_handoff_from_artifact ->
    render_plan_markdown -> normalize_plan_artifact_content where a plan with
    summary as a string (instead of a Summary object) and steps with 'details'
    (instead of 'content') was passed. Reproduce that exact shape, capture the
    PlanArtifactValidationError that the real code raises, and assert the classifier
    routes it to ARTIFACT_VALIDATION without budget debit.
    """
    malformed_payload = {
        "status": "completed",
        "summary": (
            "Development pass plan for the verifier/test fixes and final verification."
        ),
        "steps": [
            {
                "title": "Step 1: Fix verification command",
                "details": "Align tests/test_verify",
            },
            {
                "title": "Step 2: Fix policy loader",
                "details": "Tighten tests/test_policy",
            },
        ],
    }

    with pytest.raises(PlanArtifactValidationError) as exc_info:
        render_plan_markdown(malformed_payload)

    classifier = FailureClassifier()
    failure = classifier.classify(exc_info.value, phase="development", agent="codex")

    assert failure.category == FailureCategory.ARTIFACT_VALIDATION
    assert failure.counts_against_budget is False


def test_plan_artifact_validation_error_via_controller_does_not_debit_budget() -> None:
    """A PlanArtifactValidationError raised by the real normalize path must not
    debit the agent budget when surfaced through RecoveryController (mirrors the
    prompt's recovery handling).
    """
    malformed_payload: dict[str, object] = {
        "status": "completed",
        "summary": "Development pass plan",
        "steps": [{"title": "Step 1", "details": "do stuff"}],
    }
    with pytest.raises(PlanArtifactValidationError) as exc_info:
        normalize_plan_artifact_content(malformed_payload)
    real_exc = exc_info.value

    registry = AgentBudgetRegistry().set_budget("development", "codex", max_retries=3)
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, budget_registry=registry)
    )
    state = _make_state(["codex"])

    _, _, evt = controller.handle(
        state, real_exc, FailureContext(phase="development", agent="codex")
    )

    assert evt.category == "artifact_validation"  # lowercase per existing convention
    assert evt.counted_against_budget is False
    budget_state = controller.budget_registry.get("development", "codex")
    assert budget_state is not None
    assert budget_state.consumed == 0


@pytest.mark.parametrize(
    ("normalize_target", "malformed_payload", "expected_type_name"),
    [
        (
            "ralph.mcp.artifacts.plan:normalize_plan_artifact_content",
            {"summary": "not-an-object", "steps": [{"title": "x", "details": "y"}]},
            "PlanArtifactValidationError",
        ),
        (
            (
                "ralph.mcp.artifacts.development_result:"
                "normalize_development_result_content"
            ),
            {"status": "completed"},
            "DevelopmentResultValidationError",
        ),
        (
            "ralph.mcp.artifacts.typed_artifacts:normalize_issues_content",
            {"status": "issues_found", "summary": "x"},
            "TypedArtifactValidationError",
        ),
        (
            (
                "ralph.mcp.artifacts.smoke_test_result:"
                "normalize_smoke_test_result_content"
            ),
            {"status": "completed", "summary": "x", "output_file": "x"},
            "SmokeTestResultValidationError",
        ),
        (
            "ralph.mcp.artifacts.product_spec:normalize_product_spec_content",
            {
                "title": "x",
                "scope": "x",
                "goals": [],
                "users": [],
                "success_criteria": [],
            },
            "ProductSpecValidationError",
        ),
        (
            "ralph.pipeline.work_units:parse_work_units_from_artifact",
            {
                "work_units": [
                    {"unit_id": "u1", "description": "x"},
                    {"unit_id": "u1", "description": "y"},
                ]
            },
            "WorkUnitsValidationError",
        ),
    ],
    ids=[
        "PlanArtifactValidationError",
        "DevelopmentResultValidationError",
        "TypedArtifactValidationError",
        "SmokeTestResultValidationError",
        "ProductSpecValidationError",
        "WorkUnitsValidationError",
    ],
)
def test_all_typed_artifact_validation_errors_route_to_artifact_validation(
    normalize_target: str,
    malformed_payload: dict,
    expected_type_name: str,
) -> None:
    """For every typed ValidationError class produced by an artifact normalize function,
    verify the real exception (driven through the real code path) classifies as
    ARTIFACT_VALIDATION without budget debit.
    """
    module_name, _, func_name = normalize_target.partition(":")
    module = importlib.import_module(module_name)
    normalize_fn = getattr(module, func_name)

    with pytest.raises(Exception) as exc_info:
        normalize_fn(malformed_payload)
    raised = exc_info.value
    assert type(raised).__name__ == expected_type_name, (
        f"Expected {expected_type_name} from {normalize_target} "
        f"but got {type(raised).__name__}"
    )

    classifier = FailureClassifier()
    failure = classifier.classify(raised, phase="development", agent="claude")

    assert failure.category == FailureCategory.ARTIFACT_VALIDATION, (
        f"{expected_type_name} classified as {failure.category}, "
        "expected ARTIFACT_VALIDATION"
    )
    assert failure.counts_against_budget is False


def test_development_result_validation_error_constructed_directly_still_routes_correctly() -> (
    None
):
    """Even when a DevelopmentResultValidationError is constructed directly, the
    type-name dispatch still routes it correctly (defense-in-depth).
    """
    classifier = FailureClassifier()
    failure = classifier.classify(
        DevelopmentResultValidationError("bad shape"),
        phase="development",
        agent="claude",
    )
    assert failure.category == FailureCategory.ARTIFACT_VALIDATION
    assert failure.counts_against_budget is False


def test_enospc_oserror_with_filename_arg_from_failed_terminal_is_environmental() -> (
    None
):
    """OSError(ENOSPC) with a filename arg, raised in failed_terminal with agent=None
    (mirrors the prompt's exact traceback), must classify as ENVIRONMENTAL.
    """
    classifier = FailureClassifier()
    exc = OSError(
        errno.ENOSPC, "No space left on device", "/Users/x/Projects/Foo/.agent/start_commit"
    )

    failure = classifier.classify(exc, phase="failed_terminal", agent=None)

    assert failure.category == FailureCategory.ENVIRONMENTAL
    assert failure.counts_against_budget is False
