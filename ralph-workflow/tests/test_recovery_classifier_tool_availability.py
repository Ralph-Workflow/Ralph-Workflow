from __future__ import annotations

import pytest

from ralph.mcp.tools.bridge._tool_bridge_error import ToolBridgeError
from ralph.mcp.tools.bridge._tool_dispatch_error import ToolDispatchError
from ralph.mcp.tools.bridge._tool_registration_error import ToolRegistrationError
from ralph.recovery.classified_failure import ClassifiedFailure
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.failure_classifier import (
    _TOOL_AVAILABILITY_SUBSTRINGS,
    FailureClassifier,
)


def _make_exc(message: str, exc_type: type[Exception] = RuntimeError) -> Exception:
    return exc_type(message)


def test_classifier_routes_no_such_tool_available_to_reset_tool_registry() -> None:
    live_error_message = (
        "<tool_use_error>Error: No such tool available: mcp__ralph__read_file</tool_use_error>"
    )
    exc = _make_exc(live_error_message, RuntimeError)
    classified = FailureClassifier().classify(exc, phase="development", agent="claude/haiku")
    assert isinstance(classified, ClassifiedFailure)
    assert classified.reset_tool_registry is True
    assert classified.category == FailureCategory.AGENT
    assert classified.counts_against_budget is True


def test_classifier_routes_no_such_tool_available_case_insensitively() -> None:
    exc = _make_exc("NO SUCH TOOL AVAILABLE: mcp__ralph__read_file", RuntimeError)
    classified = FailureClassifier().classify(exc, phase="development", agent="claude/haiku")
    assert classified.reset_tool_registry is True


def test_classifier_routes_no_such_tool_available_in_stderr_surface() -> None:

    class _StderrError(RuntimeError):
        def __init__(self, message: str, stderr: str) -> None:
            super().__init__(message)
            self.stderr = stderr

    exc = _StderrError("Tool call failed", "no such tool available: mcp__ralph__read_file")
    classified = FailureClassifier().classify(exc, phase="development", agent="claude/haiku")
    assert classified.reset_tool_registry is True


def test_classifier_routes_runtime_tool_dispatch_error_to_reset_tool_registry() -> None:
    exc = ToolDispatchError("Tool 'read_file' is not registered")
    classified = FailureClassifier().classify(exc, phase="development", agent="claude/haiku")
    assert classified.reset_tool_registry is True


def test_classifier_routes_empty_response_after_tool_result_to_reset_tool_registry() -> None:
    exc = RuntimeError("Model returned an empty response with no tool calls")
    exc.parsed_output = ['{"type":"tool_result","tool":"read_file","result":{"ok":true}}']
    classified = FailureClassifier().classify(exc, phase="development", agent="claude/haiku")
    assert classified.reset_tool_registry is True
    assert classified.category == FailureCategory.AGENT
    assert classified.counts_against_budget is True


def test_classifier_does_not_route_programming_time_tool_registration_error() -> None:
    exc = ToolRegistrationError("Tool 'read_file' is not registered")
    classified = FailureClassifier().classify(exc, phase="development", agent="claude/haiku")
    assert classified.reset_tool_registry is False
    assert classified.reset_tool_registry is False


def test_classifier_does_not_route_unrelated_tool_bridge_error() -> None:

    class _CapDeniedError(ToolBridgeError):
        pass

    exc = _CapDeniedError("Tool 'read_file' requires capability 'workspace.read'")
    classified = FailureClassifier().classify(exc, phase="development", agent="claude/haiku")
    assert classified.reset_tool_registry is False


def test_classifier_unrelated_message_does_not_trigger_reset() -> None:
    exc = _make_exc("Connection refused by upstream proxy", RuntimeError)
    classified = FailureClassifier().classify(exc, phase="development", agent="claude/haiku")
    assert classified.reset_tool_registry is False


def test_classifier_does_not_use_literal_dots_substring() -> None:
    for substring in _TOOL_AVAILABILITY_SUBSTRINGS:
        assert "Tool ... is not registered" not in substring, (
            f"degenerate literal-dots substring found: {substring!r}"
        )


def test_tool_availability_substrings_constant_is_exact() -> None:
    assert _TOOL_AVAILABILITY_SUBSTRINGS == ("no such tool available",)


@pytest.mark.parametrize(
    ("message", "expected_reset"),
    [
        ("No such tool available: mcp__ralph__read_file", True),
        ("no such tool available: mcp__ralph__read_file", True),
        ("No Such Tool Available: mcp__ralph__read_file", True),
        (
            "<tool_use_error>Error: No such tool available: mcp__ralph__read_file</tool_use_error>",
            True,
        ),
        ("Model returned an empty response with no tool calls", False),
        ("Connection refused", False),
        ("Session not found", False),
        ("Tool 'read_file' requires capability 'workspace.read'", False),
    ],
)
def test_classifier_substring_routing(message: str, expected_reset: bool) -> None:
    classified = FailureClassifier().classify(
        _make_exc(message, RuntimeError),
        phase="development",
        agent="claude/haiku",
    )
    assert classified.reset_tool_registry is expected_reset
