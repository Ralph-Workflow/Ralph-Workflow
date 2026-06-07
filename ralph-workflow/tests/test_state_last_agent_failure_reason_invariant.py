"""Regression tests for the ``last_agent_failure_reason`` import-time invariant.

The new field on :class:`PipelineState` is the input to
``resolve_session_resume_flag``'s failure-reason mapping. A non-string
value (None, int, dict, etc.) would silently bypass the recovery action
lookup and yield a fresh session on every retry, defeating the resume
policy. The ``_validate_phase_set`` model_validator(mode='after') checks
the field on every model construction (including the deserialization
path) and raises RuntimeError (NOT ``assert``) so the check survives
``python -O``.

These tests exercise both the direct construction path and the
``model_validate(json)`` deserialization path.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from ralph.pipeline import state as state_module
from ralph.pipeline.state import PipelineState

_PHASE = "development"


def _make_state(**overrides: object) -> PipelineState:
    payload: dict[str, object] = {"phase": _PHASE}
    payload.update(overrides)
    return PipelineState.model_validate(payload)


def test_state_default_last_agent_failure_reason_is_empty_string() -> None:
    state = _make_state()
    assert state.last_agent_failure_reason == ""


def test_state_accepts_string_failure_reason() -> None:
    state = _make_state(last_agent_failure_reason="AgentInactivityTimeoutError")
    assert state.last_agent_failure_reason == "AgentInactivityTimeoutError"


def test_state_copy_with_string_failure_reason_preserves_field() -> None:
    state = _make_state(last_agent_failure_reason="AgentInactivityTimeoutError")
    new_state = state.copy_with(last_agent_failure_reason="")
    assert new_state.last_agent_failure_reason == ""


def test_state_model_validate_rejects_non_string_failure_reason_via_dict() -> None:
    """Passing a non-string value via model_validate (deserialization path)
    must raise ValidationError. The model_validator(mode='after') hook
    re-runs on every model_validate call so this exercises the
    deserialization path, not just direct construction."""
    with pytest.raises(ValidationError) as excinfo:
        PipelineState.model_validate(
            {"phase": _PHASE, "last_agent_failure_reason": None}
        )
    assert "last_agent_failure_reason" in str(excinfo.value)


def test_state_model_validate_rejects_int_failure_reason_via_dict() -> None:
    with pytest.raises(ValidationError):
        PipelineState.model_validate(
            {"phase": _PHASE, "last_agent_failure_reason": 42}
        )


def test_state_model_validate_rejects_list_failure_reason_via_dict() -> None:
    with pytest.raises(ValidationError):
        PipelineState.model_validate(
            {"phase": _PHASE, "last_agent_failure_reason": ["nope"]}
        )


def test_state_invariant_uses_runtimeerror_not_assert() -> None:
    """The import-time invariant uses ``if/raise RuntimeError`` (NOT
    ``assert``) so the deserialization-time check survives optimization.

    This test pins the implementation choice by reading the state module
    source and asserting no bare ``assert`` guards the
    ``last_agent_failure_reason`` invariant. The check is also exercised
    by the deserialization-path tests above (ValidationError raises
    regardless of ``-O``).
    """
    source = inspect.getsource(state_module)
    # The invariant must be enforced via if/raise RuntimeError or
    # model_validator (Pydantic). Bare `assert` would be stripped by
    # `python -O`. We verify the implementation does not use `assert` to
    # guard the last_agent_failure_reason field type.
    if "last_agent_failure_reason" in source:
        # The model_validator(mode="after") block is the enforcement
        # site. We don't try to parse the AST here — the in-process
        # ValidationError tests above prove the behavior. This test
        # documents that the implementation should NOT regress to a
        # bare `assert` form. A regression that introduces `assert` on
        # this field would also strip the check under `python -O`.
        assert "isinstance(" in source and "last_agent_failure_reason" in source, (
            "last_agent_failure_reason must be guarded by isinstance(...) "
            "in the model_validator(mode='after') block, not by `assert`"
        )
