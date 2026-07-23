"""Backend parity test for the agent-event renderer.

AC-07 contract: the same logical event produces byte-identical
rendered output regardless of which agent backend (claude, codex,
opencode) emitted the source line. The normalize boundary in
:mod:`ralph.display.agent_event_renderer` removes agent-specific
quirks so the registry produces the same rich Text for every
backend-shaped ``AgentOutputLine``.

We feed three parser-shaped inputs through the registry, render each
under a no-color display context, and assert the rendered plain-text
is identical across backends.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

if TYPE_CHECKING:
    from ralph.agents.parsers.agent_output_line import AgentOutputLine
    from ralph.display.agent_activity_event import AgentActivityEvent
    from ralph.display.context import DisplayContext

from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.activity_model import ActivityProvider
from ralph.display.agent_event_renderer import (
    normalize_event_from_agent_output_line,
    render_event,
    render_event_kind_text,
)
from ralph.display.context import make_display_context
from ralph.display.tool_args import friendly_tool_name

pytestmark = pytest.mark.timeout_seconds(5)


def _ctx() -> DisplayContext:
    console = Console(
        file=io.StringIO(), force_terminal=False, color_system=None, width=200
    )
    return make_display_context(console=console)


def _agent_output_line(name: str, content: str, **metadata: object) -> AgentOutputLine:
    """Build an AgentOutputLine-shaped object using the real type."""
    from ralph.agents.parsers import AgentOutputLine

    return AgentOutputLine(type=name, content=content, metadata=metadata)


def _backend_event(
    provider: ActivityProvider,
    parser_type: str,
    content: str,
    **metadata: object,
) -> AgentActivityEvent:
    """Normalize a parser line from a given backend and provider."""
    line = _agent_output_line(parser_type, content, **metadata)
    return normalize_event_from_agent_output_line(
        line, provider=provider, unit_id="unit"
    )


def test_tool_use_renders_identically_across_backends() -> None:
    """Same logical tool call -> same rendered text for all backends."""
    ctx = _ctx()
    # All backends agree on the raw tool_use shape: name + input dict.
    shared_metadata: dict[str, object] = {
        "input": {"path": "/tmp/example.py", "command": "ls"},
    }
    contents = {
        ActivityProvider.CLAUDE: "mcp__ralph__read_file",
        ActivityProvider.CODEX: "mcp__ralph__read_file",
        ActivityProvider.OPENCODE: "mcp__ralph__read_file",
    }
    rendered_plains: dict[str, str] = {}
    for provider, content in contents.items():
        event = _backend_event(
            provider, "tool_use", content, metadata=shared_metadata
        )
        rendered = render_event(event, ctx)
        rendered_plains[provider.value] = rendered.plain
    # Backend parity: ALL three backends produce the same plain text
    assert rendered_plains["claude"] == rendered_plains["codex"]
    assert rendered_plains["codex"] == rendered_plains["opencode"]
    # And the rendered text uses the friendly tool name, not the
    # mcp__ralph__ prefix.
    rendered = render_event(
        _backend_event(
            ActivityProvider.CLAUDE,
            "tool_use",
            "mcp__ralph__read_file",
            metadata=shared_metadata,
        ),
        ctx,
    )
    assert "ralph.read_file" in rendered.plain
    # Defense in depth: confirm the friendly name is what we expect.
    assert friendly_tool_name("mcp__ralph__read_file") == "ralph.read_file"


def test_error_renders_identically_across_backends() -> None:
    """Error events normalize to the same ERROR kind regardless of backend."""
    ctx = _ctx()
    rendered_plain: dict[str, str] = {}
    for provider in (
        ActivityProvider.CLAUDE,
        ActivityProvider.CODEX,
        ActivityProvider.OPENCODE,
    ):
        event = _backend_event(provider, "error", "permission denied")
        rendered = render_event(event, ctx)
        rendered_plain[provider.value] = rendered.plain
    assert rendered_plain["claude"] == rendered_plain["codex"]
    assert rendered_plain["codex"] == rendered_plain["opencode"]
    assert "permission denied" in rendered_plain["claude"]


def test_text_renders_identically_across_backends() -> None:
    """Plain text rendering is backend-agnostic."""
    ctx = _ctx()
    rendered_plains: dict[str, str] = {}
    for provider in (
        ActivityProvider.CLAUDE,
        ActivityProvider.CODEX,
        ActivityProvider.OPENCODE,
    ):
        event = _backend_event(provider, "text", "Hello, world.")
        rendered = render_event(event, ctx)
        rendered_plains[provider.value] = rendered.plain
    assert rendered_plains["claude"] == rendered_plains["codex"] == rendered_plains["opencode"]
    assert rendered_plains["codex"] == rendered_plains["opencode"]


def test_render_event_kind_text_backend_neutral_tool_use() -> None:
    """The plain-text path also produces identical output across backends."""
    metadata: dict[str, object] = {"input": {"path": "src/foo.py"}}
    plains: dict[str, str] = {}
    for provider in (
        ActivityProvider.CLAUDE,
        ActivityProvider.CODEX,
        ActivityProvider.OPENCODE,
    ):
        event = _backend_event(
            provider,
            "tool_use",
            "mcp__ralph__read_file",
            metadata=metadata,
        )
        content = event.content if event.content is not None else ""
        source = event.source if event.source is not None else ""
        plains[provider.value] = render_event_kind_text(
            ActivityEventKind.TOOL_USE,
            content,
            timestamp=event.timestamp,
            metadata=metadata,
            agent_name=source,
        )
    assert plains["claude"] == plains["codex"]
    assert plains["codex"] == plains["opencode"]
