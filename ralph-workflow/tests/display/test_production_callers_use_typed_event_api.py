"""Production-callers-use-typed-event-API regression guard.

After the wt-028-display consolidation the canonical rendering
abstraction is :func:`ralph.display.agent_event_renderer.render_event`,
which consumes a typed :class:`AgentActivityEvent`. Every production
ingestion site must construct/normalize the typed event BEFORE calling
the registry, not pass loose ``(kind, content, metadata)`` arguments
through the legacy ``render_event_kind_text`` adapter.

This test guards that contract by introspecting the production
ingestion sites directly:

* :mod:`ralph.pipeline.activity_stream` (``_render_agent_activity_line``)
* :mod:`ralph.display.parallel_display` (``_emit_activity_event``)
* :mod:`ralph.display.activity_model` (``render_event_line``)

Each must reference the typed normalizer
(:func:`normalize_event_from_agent_output_line` or the new
:func:`make_event_for_emit`) and the canonical renderer
(:func:`render_event`).

The previous attempt shipped a development_result that claimed
"production references to the typed normalizer/event API" while the
production code still called :func:`render_event_kind_text` with
loose args. This regression test prevents that drift: a regression
that drops the typed-event construction is detected here as a
verify-gate failure.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout_seconds(5)


_RALPH_ROOT = Path(__file__).resolve().parents[2] / "ralph"


def _parse_module(rel_path: str) -> ast.Module:
    """Parse a ralph source file into an AST for structural assertions."""
    return ast.parse((_RALPH_ROOT / rel_path).read_text(encoding="utf-8"))


def _qualname(node: ast.AST) -> str | None:
    """Return the dotted name of a function/async-function definition."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name
    return None


def _function_calls(module: ast.Module, function_name: str) -> set[str]:
    """Return the set of callee names referenced inside ``function_name``'s body."""
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return _collect_call_names(node)
    msg = f"function {function_name!r} not found in module"
    raise AssertionError(msg)


def _collect_call_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            callee = child.func
            if isinstance(callee, ast.Name):
                names.add(callee.id)
            elif isinstance(callee, ast.Attribute):
                names.add(callee.attr)
    return names


def test_activity_stream_uses_typed_normalizer_and_render_event() -> None:
    """_render_agent_activity_line must call normalize + render_event directly.

    Regression: the prior pass kept the function calling the loose
    ``render_event_kind_text`` adapter with a manually-split kind +
    content + metadata tuple. The typed-event contract requires the
    function to construct/normalize an ``AgentActivityEvent`` and
    call ``render_event`` directly.
    """
    module = _parse_module("pipeline/activity_stream.py")
    calls = _function_calls(module, "_render_agent_activity_line")
    assert "normalize_event_from_agent_output_line" in calls, (
        "activity_stream._render_agent_activity_line must call "
        "normalize_event_from_agent_output_line to construct a typed event"
    )
    assert "render_event" in calls, (
        "activity_stream._render_agent_activity_line must call "
        "render_event directly (not the legacy render_event_kind_text adapter)"
    )


def test_parallel_display_uses_typed_event_api() -> None:
    """_emit_activity_event must build an AgentActivityEvent and call render_event."""
    module = _parse_module("display/parallel_display.py")
    calls = _function_calls(module, "_emit_activity_event")
    # Either the typed normalizer (preferred) or the public
    # ``make_event_for_emit`` helper is acceptable as the typed-event
    # construction call.
    typed_construction = {
        "make_event_for_emit",
        "normalize_event_from_agent_output_line",
    }
    assert calls & typed_construction, (
        "parallel_display._emit_activity_event must construct a typed "
        "AgentActivityEvent (via make_event_for_emit or "
        "normalize_event_from_agent_output_line) at the ingestion "
        "boundary, not pass loose args to render_event_kind_text"
    )
    assert "render_event" in calls, (
        "parallel_display._emit_activity_event must call "
        "render_event directly (not the legacy render_event_kind_text adapter)"
    )


def test_activity_model_uses_typed_event_api() -> None:
    """render_event_line must build a typed event and call render_event."""
    module = _parse_module("display/activity_model.py")
    calls = _function_calls(module, "render_event_line")
    typed_construction = {
        "make_event_for_emit",
        "normalize_event_from_agent_output_line",
    }
    assert calls & typed_construction, (
        "activity_model.render_event_line must construct a typed "
        "AgentActivityEvent (via make_event_for_emit or "
        "normalize_event_from_agent_output_line) at the ingestion "
        "boundary, not pass loose args to render_event_kind_text"
    )
    assert "render_event" in calls, (
        "activity_model.render_event_line must call render_event directly"
    )


def test_three_production_paths_share_one_renderer_call() -> None:
    """All three production renderers reference the same canonical render_event.

    The "single rendering abstraction exists" AC-06 contract requires
    all three production call sites to route through the same
    ``render_event`` function, not a per-path formatter. This test
    fails if a future regression replaces one path's ``render_event``
    call with a hand-rolled formatter.
    """
    paths_and_functions = (
        ("pipeline/activity_stream.py", "_render_agent_activity_line"),
        ("display/parallel_display.py", "_emit_activity_event"),
        ("display/activity_model.py", "render_event_line"),
    )
    missing: list[str] = []
    for rel_path, fn_name in paths_and_functions:
        module = _parse_module(rel_path)
        calls = _function_calls(module, fn_name)
        if "render_event" not in calls:
            missing.append(f"{rel_path}:{fn_name}")
    assert not missing, (
        "production renderers missing render_event call (must use the "
        f"single canonical abstraction): {missing}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
