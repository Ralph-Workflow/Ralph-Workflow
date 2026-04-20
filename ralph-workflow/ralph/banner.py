"""CLI banner display helpers for Ralph."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from types import ModuleType

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

    def print(self, *_objects: object, **kwargs: object) -> None:
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


class _RichConsoleClassProto(Protocol):
    """Protocol for rich.Console class (constructor)."""

    def __call__(self, **kwargs: object) -> SupportsPrint: ...


def _module_attr(module: ModuleType, attribute: str) -> object:
    namespace = cast("dict[str, object]", module.__dict__)
    return namespace[attribute]


def _load_rich_components() -> tuple[
    _RichConsoleClassProto, _RichGroupProto, _RichPanelProto, _RichTextProto
]:
    """Load rich classes lazily so static analysis does not depend on local env setup."""
    console_module = import_module("rich.console")
    panel_module = import_module("rich.panel")
    text_module = import_module("rich.text")
    return (
        cast("_RichConsoleClassProto", _module_attr(console_module, "Console")),
        cast("_RichGroupProto", _module_attr(console_module, "Group")),
        cast("_RichPanelProto", _module_attr(panel_module, "Panel")),
        cast("_RichTextProto", _module_attr(text_module, "Text")),
    )


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
    if console is not None:
        console_instance: SupportsPrint = console
    else:
        console_instance = _load_rich_components()[0]()
    console_instance.print(render_banner(version=version))
