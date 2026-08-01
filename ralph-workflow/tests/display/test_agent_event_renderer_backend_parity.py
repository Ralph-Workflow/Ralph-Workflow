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
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

if TYPE_CHECKING:
    from ralph.agents.parsers.agent_output_line import AgentOutputLine
    from ralph.display.agent_activity_event import AgentActivityEvent
    from ralph.display.context import DisplayContext

from ralph.display import activity_model
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

# Backend-parity tests render the same logical event through three
# provider normalizers and compare the rendered plain text byte-for-byte.
# ``make_event`` (in ralph/display/activity_model.py) stamps every event
# with a fresh ``datetime.now(UTC).isoformat()`` at construction, so the
# three backends can otherwise pick up adjacent-second timestamps when the
# loop crosses a wall-clock second boundary. Pin ``datetime.now(UTC)`` to
# a single fixed instant for the whole module so the timestamp cue in the
# rendered line is identical across providers (the parity contract the
# tests assert).
_FROZEN_TIMESTAMP: datetime = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _freeze_event_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the event-construction clock to a single fixed UTC instant.

    ``ralph.display.activity_model.make_event`` calls
    ``datetime.now(UTC).isoformat()`` at every event construction. The
    backend-parity tests below build three such events in a tight loop
    and compare the rendered plain text; a second-boundary crossing
    would split the timestamps and break the parity assertion. Pinning
    the clock at module import time isn't safe (the clock keeps moving
    between import and the test body), so use an autouse fixture.
    """

    class _FrozenDatetime:
        @staticmethod
        def now(tz: object = None) -> datetime:
            assert tz is UTC
            return _FROZEN_TIMESTAMP

    monkeypatch.setattr(activity_model, "datetime", _FrozenDatetime)


def _ctx() -> DisplayContext:
    console = Console(file=io.StringIO(), force_terminal=False, color_system=None, width=200)
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
    return normalize_event_from_agent_output_line(line, provider=provider, unit_id="unit")


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
        event = _backend_event(provider, "tool_use", content, metadata=shared_metadata)
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
    # DA-003 (wt-028-display P1 / AC-08 / S-13): pin every backend
    # iteration to the same fixed timestamp so the 1-second-resolution
    # ``HH:MM:SS`` formatter cannot drift the rendered text across
    # iterations of ``CLAUDE`` -> ``CODEX`` -> ``OPENCODE``. Without
    # this pin the test is wall-clock-bound and intermittently
    # fails under parallel load when iteration crosses a second
    # boundary; the assertion is purely about backend parity, not
    # the live timestamp, so a deterministic timestamp preserves
    # the contract.
    fixed_timestamp = "2024-01-01T00:00:00+00:00"
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
            timestamp=fixed_timestamp,
            metadata=metadata,
            agent_name=source,
        )
    assert plains["claude"] == plains["codex"]
    assert plains["codex"] == plains["opencode"]
