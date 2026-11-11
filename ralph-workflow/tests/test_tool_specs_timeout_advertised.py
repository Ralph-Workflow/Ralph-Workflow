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
    applied = parse_exec_params({"command": "true"}).timeout_ms
    assert _timeout_schema(EXEC_TOOL)["default"] == applied


def test_exec_family_does_not_advertise_the_stale_30s_default() -> None:
    for name in _EXEC_FAMILY:
        spec = _spec(name)
        blob = (spec.metadata.definition.description + str(_timeout_schema(name))).lower()
        assert "default: 30000" not in blob, name
        assert "default 30000" not in blob, name
        assert str(EXEC_DEFAULT_TIMEOUT_MS) in blob, name


def test_exec_family_hint_teaches_the_two_meanings_of_a_timeout() -> None:
    for name in _EXEC_FAMILY:
        top = _spec(name).metadata.definition.description.lower()
        prop = str(_timeout_schema(name)["description"]).lower()
        assert "is_error" in top, name
        assert "timeout_ms" in top, name
        assert "raise" in prop and "timeout_ms" in prop, name
        assert any(word in prop for word in ("loop", "stuck", "hang", "deadlock")), name


def test_exec_family_top_level_description_within_hint_budget() -> None:
    for name in _EXEC_FAMILY:
        assert len(_spec(name).metadata.definition.description) <= 500, name
