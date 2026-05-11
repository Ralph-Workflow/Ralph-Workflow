"""CLI banner display helpers for Ralph Workflow."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from types import ModuleType

    from ralph.display.context import DisplayContext

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

    def print(self, *_objects: object, **_kwargs: object) -> None:
        """Print rich renderables."""


class _RichTextProto(Protocol):
    """Protocol for rich.Text class."""

    def __call__(self, text: str = "", *, style: str = "") -> object: ...


class _RichPanelProto(Protocol):
    """Protocol for rich.Panel class."""

    def fit(self, _renderable: object, **kwargs: object) -> object: ...


class _RichGroupProto(Protocol):
    """Protocol for rich.Group class."""

    def __call__(self, *_renderables: object) -> object: ...


def _module_attr(module: ModuleType, attribute: str) -> object:
    namespace = cast("dict[str, object]", module.__dict__)
    return namespace[attribute]


def _load_rich_components() -> tuple[
    _RichGroupProto, _RichPanelProto, _RichTextProto
]:
    """Load rich classes lazily so static analysis does not depend on local env setup."""
    console_module = import_module("rich.console")
    panel_module = import_module("rich.panel")
    text_module = import_module("rich.text")
    return (
        cast("_RichGroupProto", _module_attr(console_module, "Group")),
        cast("_RichPanelProto", _module_attr(panel_module, "Panel")),
        cast("_RichTextProto", _module_attr(text_module, "Text")),
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
    console: SupportsPrint | None = None,
    version: str = __version__,
) -> None:
    """Print the Ralph Workflow welcome banner to the provided console."""
    compact = display_context.mode == "compact"
    console_instance: SupportsPrint = (
        console if console is not None else cast("SupportsPrint", display_context.console)
    )
    console_instance.print(render_banner(version=version, compact=compact))
