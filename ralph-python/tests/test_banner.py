"""Unit tests for Ralph CLI banner display."""

from __future__ import annotations

from importlib import import_module
from io import StringIO
from typing import Any

from ralph import __version__
from ralph.banner import render_banner, show_banner


def _create_console(buffer: StringIO) -> Any:
    """Create a rich console for output capture."""
    console_cls = import_module("rich.console").Console
    return console_cls(file=buffer, force_terminal=False, width=100)


def test_render_banner_includes_ascii_art_version_and_welcome_message() -> None:
    """Rendered banner should include Ralph branding, version, and welcome copy."""
    buffer = StringIO()
    console = _create_console(buffer)
    console.print(render_banner())

    output = buffer.getvalue()
    assert "Ralph" in output
    assert __version__ in output
    assert "Welcome to Ralph Workflow" in output
    assert "PROMPT-driven agent orchestrator" in output
    assert "____" in output or "╭" in output


def test_render_banner_allows_version_override() -> None:
    """Custom versions should be rendered for testability and packaging."""
    buffer = StringIO()
    console = _create_console(buffer)
    console.print(render_banner(version="9.9.9"))

    assert "9.9.9" in buffer.getvalue()


def test_show_banner_prints_to_provided_console() -> None:
    """show_banner should print the banner to the supplied console."""
    buffer = StringIO()
    console = _create_console(buffer)

    show_banner(console=console)

    output = buffer.getvalue()
    assert "Ralph" in output
    assert "Welcome to Ralph Workflow" in output
