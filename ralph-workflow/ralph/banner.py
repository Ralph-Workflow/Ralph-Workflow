"""CLI banner display helpers for Ralph Workflow."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

from ralph import __version__
from ralph.rich_protocols import RichGroupProto, RichPanelProto, RichTextProto

if TYPE_CHECKING:
    from types import ModuleType

    from ralph.display.context import DisplayContext

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

__all__ = [
    "RichGroupProto",
    "RichPanelProto",
    "RichTextProto",
    "SupportsPrint",
    "render_banner",
    "show_banner",
]


class SupportsPrint(Protocol):
    """Protocol for rich-compatible consoles."""

    def print(self, *args: object, **kwargs: object) -> None: ...


def _module_attr(module: ModuleType, attribute: str) -> object:
    namespace = cast("dict[str, object]", module.__dict__)
    return namespace[attribute]


def _load_rich_components() -> tuple[RichGroupProto, RichPanelProto, RichTextProto]:
    """Load rich classes lazily so static analysis does not depend on local env setup."""
    console_module = import_module("rich.console")
    panel_module = import_module("rich.panel")
    text_module = import_module("rich.text")
    return (
        cast("RichGroupProto", _module_attr(console_module, "Group")),
        cast("RichPanelProto", _module_attr(panel_module, "Panel")),
        cast("RichTextProto", _module_attr(text_module, "Text")),
    )


def render_banner(
    *,
    version: str = __version__,
    compact: bool = False,
) -> object:
    """Build the Ralph Workflow welcome banner as a rich renderable."""
    group_cls, panel_cls, text_cls = _load_rich_components()

    if compact:
        welcome = text_cls(f"Ralph Workflow v{version}", style="theme.banner.welcome")
        tagline = text_cls(TAGLINE, style="theme.banner.tagline")
        return group_cls(welcome, tagline)

    banner_text = text_cls("\n".join(ASCII_ART), style="theme.banner.ascii")
    version_text = text_cls(f"v{version}", style="theme.banner.version")
    title_text = text_cls("Ralph Workflow", style="theme.banner.title")
    welcome_text = text_cls(WELCOME_MESSAGE, style="theme.banner.welcome")
    tagline_text = text_cls(TAGLINE, style="theme.banner.tagline")

    banner_panel = panel_cls.fit(
        banner_text,
        border_style="theme.banner.border",
        padding=(0, 1),
        title=title_text,
        subtitle=version_text,
    )

    return group_cls(banner_panel, welcome_text, tagline_text)


def show_banner(
    *,
    display_context: DisplayContext,
    version: str = __version__,
) -> None:
    """Print the Ralph Workflow welcome banner to ``display_context.console``."""
    compact = display_context.mode == "compact"
    cast("SupportsPrint", display_context.console).print(
        render_banner(version=version, compact=compact)
    )
