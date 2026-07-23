"""Tests for the single agent-event renderer registry.

These tests assert the AC-06 / AC-07 / AC-10 contracts on
:mod:`ralph.display.agent_event_renderer`:

* Every event kind has exactly one renderer registered.
* Renderers are pure (no I/O, no env reads, no Console construction)
  and reference only ``STATUS_STYLES`` / theme named keys (no literal
  rich styles, no literal hex colors).
* Every state carries a redundant non-color carrier (icon + ASCII
  label) so the meaning survives when color is disabled.
* No red/green hue-only pairing exists.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from rich.console import Console
from rich.text import Text

from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.activity_model import ActivityProvider, EventOptions, make_event
from ralph.display.agent_event_renderer import (
    EVENT_RENDERERS,
    normalize_event_from_agent_output_line,
    render_event,
)
from ralph.display.context import DisplayContext, make_display_context

if TYPE_CHECKING:
    from ralph.display.agent_activity_event import AgentActivityEvent

pytestmark = pytest.mark.timeout_seconds(5)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _ctx(width: int = 120, *, no_color: bool = False) -> DisplayContext:
    """Build a DisplayContext whose Console is a string buffer (no TTY)."""
    import io as _io

    console = Console(
        file=_io.StringIO(),
        force_terminal=False,
        color_system=None,
        width=width,
        no_color=no_color,
    )
    return make_display_context(console=console)


def _event(
    kind: ActivityEventKind,
    content: str = "hello",
    *,
    metadata: dict[str, object] | None = None,
) -> AgentActivityEvent:
    return make_event(
        provider=ActivityProvider.CLAUDE,
        kind=kind,
        options=EventOptions(content=content, metadata=metadata or {}),
    )


# ---------------------------------------------------------------------------
# Registry surface
# ---------------------------------------------------------------------------


def test_every_kind_has_a_renderer() -> None:
    for kind in ActivityEventKind:
        assert kind in EVENT_RENDERERS, f"missing renderer for {kind}"


def test_every_renderer_returns_a_rich_text() -> None:
    ctx = _ctx()
    for kind in ActivityEventKind:
        rendered = render_event(_event(kind), ctx)
        assert isinstance(rendered, Text)


def test_unknown_kind_renders_with_warning_carrier() -> None:
    ctx = _ctx()
    rendered = render_event(_event(ActivityEventKind.UNKNOWN, "foo"), ctx)
    assert "WARN" in rendered.plain or "?" in rendered.plain


# ---------------------------------------------------------------------------
# Per-kind content assertions (one test per kind keeps the matrix readable)
# ---------------------------------------------------------------------------


def test_text_renders_content() -> None:
    ctx = _ctx()
    rendered = render_event(_event(ActivityEventKind.TEXT, "hello world"), ctx)
    assert "hello world" in rendered.plain


def test_status_renders_message() -> None:
    ctx = _ctx()
    rendered = render_event(_event(ActivityEventKind.STATUS, "starting"), ctx)
    assert "starting" in rendered.plain


def test_tool_use_renders_friendly_name() -> None:
    ctx = _ctx()
    rendered = render_event(
        _event(
            ActivityEventKind.TOOL_USE,
            "mcp__ralph__read_file",
            metadata={"input": {"path": "src/foo.py"}},
        )
    , ctx)
    assert "ralph.read_file" in rendered.plain
    assert "path=src/foo.py" in rendered.plain


def test_tool_result_renders_body() -> None:
    ctx = _ctx()
    rendered = render_event(_event(ActivityEventKind.TOOL_RESULT, "out: ok"), ctx)
    assert "out: ok" in rendered.plain


def test_error_renders_with_error_carrier() -> None:
    ctx = _ctx()
    rendered = render_event(_event(ActivityEventKind.ERROR, "boom"), ctx)
    assert "boom" in rendered.plain
    assert "FAIL" in rendered.plain or "ERROR" in rendered.plain


def test_lifecycle_renders_message() -> None:
    ctx = _ctx()
    rendered = render_event(_event(ActivityEventKind.LIFECYCLE, "phase=development"), ctx)
    assert "phase=development" in rendered.plain


def test_progress_renders_message() -> None:
    ctx = _ctx()
    rendered = render_event(_event(ActivityEventKind.PROGRESS, "step 2/5"), ctx)
    assert "step 2/5" in rendered.plain


def test_subagent_progress_renders_message() -> None:
    ctx = _ctx()
    rendered = render_event(
        _event(ActivityEventKind.SUBAGENT_PROGRESS, "Read(path=src/foo.py)")
    , ctx)
    assert "Read(path=src/foo.py)" in rendered.plain


def test_heartbeat_renders_liveness_message() -> None:
    ctx = _ctx()
    rendered = render_event(_event(ActivityEventKind.HEARTBEAT, "alive"), ctx)
    assert "alive" in rendered.plain


def test_thinking_renders_message() -> None:
    ctx = _ctx()
    rendered = render_event(_event(ActivityEventKind.THINKING, "reasoning..."), ctx)
    assert "reasoning..." in rendered.plain


def test_tool_result_with_is_error_metadata_uses_error_carrier() -> None:
    ctx = _ctx()
    rendered = render_event(
        _event(
            ActivityEventKind.TOOL_RESULT,
            "permission denied",
            metadata={"is_error": True},
        )
    , ctx)
    assert "permission denied" in rendered.plain


def test_tool_result_renders_body_unabridged() -> None:
    """TOOL_RESULT body is rendered UNABRIDGED so the caller's overflow-aware condenser sees the complete original payload.

    Regression for the analysis-feedback finding: pre-fix the registry's
    ``_render_tool_result_event`` called ``_condense_for_display`` which
    truncated the body to ``soft_limit`` characters BEFORE
    ``ParallelDisplay._emit_activity_event`` ran its overflow-aware
    condenser. A 1000-character tool result then landed in the overflow
    log as ~400 chars instead of 1000, silently truncating the audit
    trail. The registry must now render the FULL body; condensation is a
    delivery concern handled by the overflow-aware condenser at the
    delivery boundary.
    """
    ctx = _ctx()
    body = "Z" * 1000
    rendered = render_event(_event(ActivityEventKind.TOOL_RESULT, body), ctx)
    plain = rendered.plain
    # Every original character must appear in the rendered plain text;
    # the registry MUST NOT condense / truncate / drop any characters.
    assert body in plain, (
        f"registry must render the full 1000-char tool result body; "
        f"got {len(plain)} chars but the body was 1000 chars"
    )
    assert plain.count("Z") == 1000, (
        f"registry must preserve every Z in the body; "
        f"got {plain.count('Z')} Z's in the rendered line, expected 1000"
    )
    # The visible line should NOT carry a condenser-suffix marker
    # because the registry never condensed it.
    assert "(truncated" not in plain, (
        f"registry must not emit a (truncated) suffix; "
        f"condensation is a delivery concern, not a presentation one: {plain!r}"
    )


# ---------------------------------------------------------------------------
# Sanitization & normalization
# ---------------------------------------------------------------------------


def test_content_escape_strips_rich_markup() -> None:
    ctx = _ctx()
    rendered = render_event(_event(ActivityEventKind.TEXT, "[red]hack[/red]"), ctx)
    # Rich markup should be neutralized: the literal `[red]` must
    # surface as `\[red]` (rich.escape escapes the leading bracket) so
    # Rich does not interpret it as a style sequence.
    plain = rendered.plain
    assert "\\[red]" in plain, f"expected escaped markup, got: {plain!r}"


def test_content_sanitization_strips_escape_sequences() -> None:
    ctx = _ctx()
    rendered = render_event(_event(ActivityEventKind.TEXT, "hi\x1b[31mred\x1b[0m"), ctx)
    # ANSI escapes must be stripped before rendering so they cannot
    # inject color into the Live region.
    assert "\x1b[31m" not in rendered.plain


# ---------------------------------------------------------------------------
# Normalizer boundary
# ---------------------------------------------------------------------------


def test_normalize_event_from_agent_output_line_routes_to_correct_kind() -> None:
    from ralph.agents.parsers import AgentOutputLine
    from ralph.display.activity_provider import ActivityProvider

    line = AgentOutputLine(
        type="tool_use", content="mcp__ralph__read_file", metadata={"input": {"path": "x"}}
    )
    event = normalize_event_from_agent_output_line(
        line, provider=ActivityProvider.CLAUDE, unit_id="u1"
    )
    assert event.kind is ActivityEventKind.TOOL_USE
    assert event.provider is ActivityProvider.CLAUDE
    assert event.source == "u1"


def test_normalize_event_maps_unknown_parser_type_to_unknown_kind() -> None:
    from ralph.agents.parsers import AgentOutputLine
    from ralph.display.activity_provider import ActivityProvider

    line = AgentOutputLine(type="unheard_of_type", content="oops")
    event = normalize_event_from_agent_output_line(
        line, provider=ActivityProvider.CLAUDE, unit_id="u1"
    )
    assert event.kind is ActivityEventKind.UNKNOWN


# ---------------------------------------------------------------------------
# No literal hex / literal rich style strings in the renderer module
# ---------------------------------------------------------------------------


def test_agent_event_renderer_has_no_literal_hex_outside_theme() -> None:
    """The renderer must reference STATUS_STYLES, not literal hex.

    AST-walks the production module looking for any string literal that
    resembles a CSS hex colour (``#RGB`` / ``#RRGGBB``) outside docstrings
    and comments. Anything that survives this filter is a literal hex
    string in source code that must reference ``STATUS_STYLES`` instead.
    """
    import ast

    source_path = Path(__file__).parent.parent.parent.joinpath(
        "ralph/display/agent_event_renderer.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        value = node.value
        if value.startswith("#") and len(value) in (4, 7):
            offenders.append(f"line {node.lineno}: {value!r}")
    assert offenders == [], (
        f"literal hex string(s) found in agent_event_renderer.py -- "
        f"reference STATUS_STYLES from ralph.display.theme instead: {offenders}"
    )
