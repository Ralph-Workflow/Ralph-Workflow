from __future__ import annotations

import io

from rich.console import Console


class _FakeDisplay:
    def __init__(self) -> None:
        self.console = Console(file=io.StringIO(), force_terminal=False, color_system=None)

    def emit(self, unit_id: str | None, line: str) -> None:
        del unit_id, line

    def set_status(self, unit_id: str, status: object) -> None:
        del unit_id, status
