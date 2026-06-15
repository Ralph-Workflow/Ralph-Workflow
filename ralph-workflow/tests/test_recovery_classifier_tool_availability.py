"""Regression tests for the failure classifier's tool-availability route.

The post-tool-result wedge failure mode is that Claude Code emits
`<tool_use_error>Error: No such tool available: mcp__<server>__<tool></tool_use_error>`
when the backing MCP server's tools/list lost the alias. The failure
classifier must route this to ``FailureCategory.AGENT`` with
``reset_tool_registry=True`` so the next attempt rebuilds the tool
registry via ``RestartAwareMcpBridge.reset_tool_registry()``.

This file pins:

- The substring `"no such tool available"` triggers
  ``reset_tool_registry=True`` and ``category=AGENT``.
- The runtime ``ToolDispatchError("Tool 'X' is not registered")``
  triggers the same routing.
- The programming-time ``ToolRegistrationError`` does NOT trigger
  ``reset_tool_registry=True`` (it should stay on the existing
  USER_CONFIG / AMBIGUOUS path).
"""

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
    """Some agent invocations put the live error in stderr rather than
    the exception message. The classifier must inspect both surfaces."""

    class _StderrError(RuntimeError):
        def __init__(self, message: str, stderr: str) -> None:
            super().__init__(message)
            self.stderr = stderr

    exc = _StderrError("Tool call failed", "no such tool available: mcp__ralph__read_file")
    classified = FailureClassifier().classify(exc, phase="development", agent="claude/haiku")
    assert classified.reset_tool_registry is True


def test_classifier_routes_runtime_tool_dispatch_error_to_reset_tool_registry() -> None:
    """ToolDispatchError raised at ralph/mcp/tools/bridge/_tool_bridge.py:64
    is the runtime-side mirror of the live No-such-tool-available error.
    The classifier must route it to reset_tool_registry=True."""
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
    """ToolRegistrationError is the programming-time bridge-construction
    error (raised at _tool_registration_error.py:8). It is NOT a
    runtime tool-availability failure and must NOT trigger a registry
    reset — it indicates a code defect in the bridge builder."""
    exc = ToolRegistrationError("Tool 'read_file' is not registered")
    classified = FailureClassifier().classify(exc, phase="development", agent="claude/haiku")
    assert classified.reset_tool_registry is False
    # The class name is "ToolRegistrationError" which is NOT in
    # _ARTIFACT_VALIDATION_TYPE_NAMES and is NOT in the typed
    # _is_user_config_exc set, so the classifier should land in the
    # AMBIGUOUS bucket (or fall through to USER_CONFIG if the
    # message happens to match). The contract is: NOT
    # reset_tool_registry=True.
    assert classified.reset_tool_registry is False


def test_classifier_does_not_route_unrelated_tool_bridge_error() -> None:
    """A custom ToolBridgeError (e.g. capability denied) should not
    trigger a tool-registry reset."""

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
    """The existing matcher is case-insensitive literal-substring, not
    regex. A literal `'Tool ... is not registered'` substring would
    only match messages containing the literal three dots. Confirm
    the matcher does NOT use that degenerate substring.

    Note: the module documentation block references the literal-dots
    pattern as a counterexample (DO NOT DO THIS). The test allows
    that documentation comment to exist but asserts the
    `_TOOL_AVAILABILITY_SUBSTRINGS` constant itself does not contain
    the degenerate form. The separate
    `test_tool_availability_substrings_constant_is_exact` test pins the
    constant directly.
    """
    for substring in _TOOL_AVAILABILITY_SUBSTRINGS:
        assert "Tool ... is not registered" not in substring, (
            f"degenerate literal-dots substring found: {substring!r}"
        )


def test_tool_availability_substrings_constant_is_exact() -> None:
    """The _TOOL_AVAILABILITY_SUBSTRINGS constant must contain EXACTLY
    the live-Claude failure mode substring. If a developer adds a
    bare "is not registered" or "Tool ... is not registered" string
    here by accident, this test fails (per the plan's substring
    matching analysis)."""
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
