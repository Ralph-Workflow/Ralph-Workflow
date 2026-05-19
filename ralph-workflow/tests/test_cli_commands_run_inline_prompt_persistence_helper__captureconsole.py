from __future__ import annotations

from io import StringIO

from rich.console import Console
from rich.text import Text

from ralph.display.theme import RALPH_THEME


class _CaptureConsole(Console):
    """A Rich Console that also captures output in .lines."""

    def __init__(self) -> None:
        super().__init__(
            file=StringIO(),
            color_system=None,
            force_terminal=False,
            theme=RALPH_THEME,
        )
        self._string_io = self.file
        self.lines: list[str] = []

    def print(self, *args: object, **kwargs: object) -> None:
        for arg in args:
            if isinstance(arg, Text):
                self.lines.append(arg.plain)
            else:
                self.lines.append(str(arg))
        super().print(*args, **kwargs)

    def getvalue(self) -> str:
        return self._string_io.getvalue()
