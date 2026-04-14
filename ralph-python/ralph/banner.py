"""CLI banner display helpers for Ralph."""

from __future__ import annotations

from importlib import import_module
from typing import Any, Protocol

from ralph import __version__

ASCII_ART = (
    " ____       _       _     _     ",
    "|  _ \\ __ _| |_ __ | |__ | |__  ",
    "| |_) / _` | | '_ \\| '_ \\| '_ \\ ",
    "|  _ < (_| | | |_) | | | | | | |",
    "|_| \\_\\__,_|_| .__/|_| |_|_| |_|",
    "              |_|                ",
)
WELCOME_MESSAGE = "Welcome to Ralph Workflow"
TAGLINE = "PROMPT-driven agent orchestrator"


class SupportsPrint(Protocol):
    """Protocol for rich-compatible consoles."""

    def print(self, *objects: object, **kwargs: object) -> None:
        """Print rich renderables."""


def _load_rich_components() -> tuple[Any, Any, Any, Any]:
    """Load rich classes lazily so static analysis does not depend on local env setup."""
    console_module = import_module("rich.console")
    panel_module = import_module("rich.panel")
    text_module = import_module("rich.text")
    return console_module.Console, console_module.Group, panel_module.Panel, text_module.Text


def render_banner(*, version: str = __version__) -> object:
    """Build the Ralph welcome banner as a rich renderable."""
    _, group_cls, panel_cls, text_cls = _load_rich_components()

    banner_text = text_cls("\n".join(ASCII_ART), style="bold cyan")
    version_text = text_cls(f"v{version}", style="bold green")
    title_text = text_cls("Ralph", style="bold white")
    welcome_text = text_cls(WELCOME_MESSAGE, style="bold white")
    tagline_text = text_cls(TAGLINE, style="dim")

    banner_panel = panel_cls.fit(
        banner_text,
        border_style="cyan",
        padding=(0, 1),
        title=title_text,
        subtitle=version_text,
    )

    return group_cls(banner_panel, welcome_text, tagline_text)


def show_banner(*, console: SupportsPrint | None = None, version: str = __version__) -> None:
    """Print the Ralph welcome banner to the provided console."""
    console_instance = console or _load_rich_components()[0]()
    console_instance.print(render_banner(version=version))
