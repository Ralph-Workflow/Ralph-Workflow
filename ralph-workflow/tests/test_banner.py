"""Unit tests for Ralph CLI banner display."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph import version
from ralph.banner import (
    RichGroupProto,
    RichPanelProto,
    RichTextProto,
    render_banner,
    show_banner,
)
from ralph.display.context import make_display_context
from ralph.display.theme import RALPH_THEME
from ralph.rich_protocols import (
    RichGroupProto as ImportedRichGroupProto,
)
from ralph.rich_protocols import (
    RichPanelProto as ImportedRichPanelProto,
)
from ralph.rich_protocols import (
    RichTextProto as ImportedRichTextProto,
)


def _create_console(buffer: StringIO) -> Console:
    """Create a themed rich console for output capture."""
    return Console(file=buffer, force_terminal=False, width=100, theme=RALPH_THEME)


def test_render_banner_includes_ascii_art_version_and_welcome_message() -> None:
    """Rendered banner should include Ralph branding, version, and welcome copy."""
    buffer = StringIO()
    console = _create_console(buffer)
    console.print(render_banner())

    output = buffer.getvalue()
    assert "Ralph" in output
    assert version in output
    assert "Welcome to Ralph Workflow" in output
    assert "PROMPT-driven agent orchestrator" in output
    assert "____" in output or "╭" in output


def test_render_banner_allows_version_override() -> None:
    """Custom versions should be rendered for testability and packaging."""
    buffer = StringIO()
    console = _create_console(buffer)
    console.print(render_banner(version="9.9.9"))

    assert "9.9.9" in buffer.getvalue()


def test_show_banner_prints_to_display_context_console() -> None:
    """show_banner should print the banner to the display_context.console."""
    buffer = StringIO()
    console = _create_console(buffer)
    ctx = make_display_context(console=console, env={})

    show_banner(display_context=ctx)

    output = buffer.getvalue()
    assert "Ralph" in output
    assert "Welcome to Ralph Workflow" in output


def test_banner_reexports_rich_protocols_for_backward_compatibility() -> None:
    """banner should continue exporting the rich protocol types after the split."""
    assert RichTextProto is ImportedRichTextProto
    assert RichPanelProto is ImportedRichPanelProto
    assert RichGroupProto is ImportedRichGroupProto
