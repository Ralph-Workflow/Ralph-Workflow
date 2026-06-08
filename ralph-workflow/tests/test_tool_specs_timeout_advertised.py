"""The exec-family tool *hints* must advertise timeout behavior truthfully.

Two defects motivated these pins:

1. **Drift:** the exec default was raised to 90s in ``exec.py`` but the tool
   schema still hard-coded ``"default": 30000`` (and "default: 30000" prose), so
   the agent was told a stale 30s default. The schema default must equal the one
   source of truth (``EXEC_DEFAULT_TIMEOUT_MS``) and never be re-hard-coded.

2. **Ambiguity not taught:** a timeout has two distinct meanings — the command is
   legitimately long (raise ``timeout_ms``) OR the command is genuinely stuck
   (infinite loop / deadlock / blocked on input), where raising the limit only
   wastes more time and the command itself must be fixed. The decision-time hint
   must surface BOTH so the agent does not reflexively just double the timeout.

All assertions read the published tool spec — no implementation internals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.tools.bridge._specs_git_exec import git_exec_specs
from ralph.mcp.tools.exec import parse_exec_params
from ralph.mcp.tools.names import EXEC_TOOL, RAW_EXEC_TOOL, UNSAFE_EXEC_TOOL
from ralph.timeout_defaults import EXEC_DEFAULT_TIMEOUT_MS

if TYPE_CHECKING:
    from ralph.mcp.tools.bridge._tool_spec import ToolSpec

_EXEC_FAMILY = (EXEC_TOOL, UNSAFE_EXEC_TOOL, RAW_EXEC_TOOL)


def _spec(name: str) -> ToolSpec:
    for spec in git_exec_specs():
        if spec.metadata.definition.name == name:
            return spec
    raise AssertionError(f"tool spec {name!r} not found")


def _timeout_schema(name: str) -> dict[str, object]:
    schema = _spec(name).metadata.definition.input_schema
    properties = schema["properties"]
    assert isinstance(properties, dict)
    timeout = properties["timeout_ms"]
    assert isinstance(timeout, dict)
    return timeout


def test_exec_family_schema_default_matches_single_source_of_truth() -> None:
    for name in _EXEC_FAMILY:
        assert _timeout_schema(name)["default"] == EXEC_DEFAULT_TIMEOUT_MS, name


def test_advertised_exec_default_matches_the_timeout_actually_applied() -> None:
    """Behavioral no-drift pin: whatever default the exec handler applies when the
    caller omits ``timeout_ms`` must equal what the schema advertises. If a future
    edit reintroduces a literal in either place, this fails."""
    applied = parse_exec_params({"command": "true"}).timeout_ms
    assert _timeout_schema(EXEC_TOOL)["default"] == applied


def test_exec_family_does_not_advertise_the_stale_30s_default() -> None:
    for name in _EXEC_FAMILY:
        spec = _spec(name)
        blob = (spec.metadata.definition.description + str(_timeout_schema(name))).lower()
        assert "default: 30000" not in blob, name
        assert "default 30000" not in blob, name
        # The real default must be the one that appears as the stated default.
        assert str(EXEC_DEFAULT_TIMEOUT_MS) in blob, name


def test_exec_family_hint_teaches_the_two_meanings_of_a_timeout() -> None:
    for name in _EXEC_FAMILY:
        top = _spec(name).metadata.definition.description.lower()
        prop = str(_timeout_schema(name)["description"]).lower()
        # Top-level keeps a short pointer so the agent knows a timeout is terminal
        # (is_error), not a retryable protocol error.
        assert "is_error" in top, name
        assert "timeout_ms" in top, name
        # The timeout_ms property carries the full two meanings:
        # Meaning 1: legitimately long -> raise the limit.
        assert "raise" in prop and "timeout_ms" in prop, name
        # Meaning 2: the command may be genuinely stuck and must be fixed, not
        # merely retried with a bigger budget.
        assert any(word in prop for word in ("loop", "stuck", "hang", "deadlock")), name


def test_exec_family_top_level_description_within_hint_budget() -> None:
    # The wire layer enforces a 500-char cap on the tool description; pin it here
    # too so detailed timeout guidance stays in the (uncapped) property, not the
    # top-level hint.
    for name in _EXEC_FAMILY:
        assert len(_spec(name).metadata.definition.description) <= 500, name
