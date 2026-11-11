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
from ralph.pipeline.state import AgentChainState, PipelineState
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
        phase_chains={"development": AgentChainState(agents=agents, current_index=0, retries=0)},
    )


def test_unknown_exception_is_ambiguous() -> None:
    classifier = FailureClassifier()

    failure = classifier.classify(
        RuntimeError("something went wrong but not sure what"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.AMBIGUOUS
    assert failure.counts_against_budget is False


def test_generic_exception_message_ambiguous() -> None:
    classifier = FailureClassifier()

    failure = classifier.classify(
        ValueError("invalid input provided"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.AMBIGUOUS
    assert failure.counts_against_budget is False


def test_ambiguous_failure_is_not_counted_against_the_budget() -> None:
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))
    state = _make_state()

    _, _, evt = controller.handle(
        state,
        RuntimeError("something unexpected happened"),
        FailureContext(phase="development", agent="claude"),
    )

    assert evt.counted_against_budget is False
    assert evt.category == "ambiguous"

    assert evt.retry_delay_ms == 0


def test_ambiguous_failure_returns_state_without_phase_change() -> None:
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))
    state = _make_state()

    new_state, effects, evt = controller.handle(
        state,
        OSError("some system error"),
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.phase == "development"
    assert effects == []
    assert evt.counted_against_budget is False


def test_ambiguous_failure_is_flagged_in_reason() -> None:
    classifier = FailureClassifier()

    failure = classifier.classify(
        Exception("an exception without clear attribution"),
        phase="development",
        agent="claude",
    )

    assert FailureCategory.AMBIGUOUS in failure.reason or "flagged" in failure.reason.lower()


def test_ambiguous_failure_emits_warning_log() -> None:
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
        assert "flagged_for_review" in log_output.lower() or "ambiguous" in log_output.lower()
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
    classifier = FailureClassifier()

    failure = classifier.classify(
        ConnectionRefusedError("connection refused"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.ENVIRONMENTAL
    assert failure.counts_against_budget is False


def test_timeout_error_is_environmental() -> None:
    classifier = FailureClassifier()

    failure = classifier.classify(
        TimeoutError("operation timed out"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.ENVIRONMENTAL
    assert failure.counts_against_budget is False


def test_agent_inactivity_timeout_is_agent_fault() -> None:
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
    classifier = FailureClassifier()

    failure = classifier.classify(
        OSError(errno.ENOSPC, "No space left on device"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.ENVIRONMENTAL
    assert failure.counts_against_budget is False


def test_enospc_oserror_via_controller_is_not_counted_against_the_budget() -> None:
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))
    state = _make_state()

    _, _, evt = controller.handle(
        state,
        OSError(errno.ENOSPC, "No space left on device"),
        FailureContext(phase="development", agent="claude"),
    )

    assert evt.category == "environmental"
    assert evt.counted_against_budget is False
    assert evt.retry_delay_ms == 0


@pytest.mark.parametrize(
    ("normalize_target", "malformed_payload", "expected_type_name"),
    [
        (
            ("ralph.mcp.artifacts.development_result:normalize_development_result_content"),
            {"status": "done"},
            "DevelopmentResultValidationError",
        ),
        (
            "ralph.mcp.artifacts.typed_artifacts:normalize_issues_content",
            {"status": "issues_found", "summary": "x"},
            "TypedArtifactValidationError",
        ),
        (
            ("ralph.mcp.artifacts.smoke_test_result:normalize_smoke_test_result_content"),
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
    ],
    ids=[
        "DevelopmentResultValidationError",
        "TypedArtifactValidationError",
        "SmokeTestResultValidationError",
        "ProductSpecValidationError",
    ],
)
def test_all_typed_artifact_validation_errors_route_to_artifact_validation(
    normalize_target: str,
    malformed_payload: dict[str, object],
    expected_type_name: str,
) -> None:
    module_name, _, func_name = normalize_target.partition(":")
    module = importlib.import_module(module_name)
    normalize_fn = getattr(module, func_name)

    with pytest.raises(Exception) as exc_info:
        normalize_fn(malformed_payload)
    raised = exc_info.value
    assert type(raised).__name__ == expected_type_name, (
        f"Expected {expected_type_name} from {normalize_target} but got {type(raised).__name__}"
    )

    classifier = FailureClassifier()
    failure = classifier.classify(raised, phase="development", agent="claude")

    assert failure.category == FailureCategory.ARTIFACT_VALIDATION, (
        f"{expected_type_name} classified as {failure.category}, expected ARTIFACT_VALIDATION"
    )
    assert failure.counts_against_budget is False


def test_development_result_validation_error_constructed_directly_still_routes_correctly() -> None:
    classifier = FailureClassifier()
    failure = classifier.classify(
        DevelopmentResultValidationError("bad shape"),
        phase="development",
        agent="claude",
    )
    assert failure.category == FailureCategory.ARTIFACT_VALIDATION
    assert failure.counts_against_budget is False


def test_enospc_oserror_with_filename_arg_from_failed_terminal_is_environmental() -> None:
    classifier = FailureClassifier()
    exc = OSError(
        errno.ENOSPC, "No space left on device", "/Users/x/Projects/Foo/.agent/start_commit"
    )

    failure = classifier.classify(exc, phase="failed_terminal", agent=None)

    assert failure.category == FailureCategory.ENVIRONMENTAL
    assert failure.counts_against_budget is False
