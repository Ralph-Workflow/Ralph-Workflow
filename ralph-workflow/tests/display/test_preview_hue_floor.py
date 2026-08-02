"""Regression coverage for syntax previews collapsing to neutral text."""

from __future__ import annotations

import io
import re

import pytest
from rich.console import Console

from ralph.display.edit_preview import build_edit_preview

_SGR = re.compile(r"\x1b\[([0-9;]*)m")
_NEUTRAL_FOREGROUNDS = {False: "38;2;208;208;208", True: "38;2;32;32;32"}
_SOURCE = """@decorator
class Widget:
    CONSTANT = 1
    def method(self, value: int) -> str:
        module.attribute = value
        return f\"{value}\"
"""


def _hued_foreground_fraction(rendered: str, *, terminal_bg_is_light: bool) -> float:
    style = ""
    foreground_cells = 0
    hued_cells = 0
    for part in _SGR.split(rendered):
        if part and all(char.isdigit() or char == ";" for char in part):
            style = part
            continue
        visible = len(part.replace("\n", ""))
        if "38;2;" in style:
            foreground_cells += visible
            if _NEUTRAL_FOREGROUNDS[terminal_bg_is_light] not in style:
                hued_cells += visible
    assert foreground_cells, "preview emitted no styled foreground cells"
    return hued_cells / foreground_cells


@pytest.mark.parametrize("terminal_bg_is_light", [False, True])
def test_preview_regression_identifier_heavy_source_has_hued_foreground_mass(
    terminal_bg_is_light: bool,
) -> None:
    """S-2: identifiers must not collapse a source preview to neutral text."""
    preview = build_edit_preview(
        "write_file",
        {"path": "widget.py", "content": _SOURCE},
        width=80,
        terminal_bg_is_light=terminal_bg_is_light,
    )
    assert preview is not None
    output = io.StringIO()
    Console(file=output, force_terminal=True, color_system="truecolor", width=80).print(preview)
    fraction = _hued_foreground_fraction(
        output.getvalue(), terminal_bg_is_light=terminal_bg_is_light
    )
    assert fraction >= 0.5, f"hued foreground fraction was {fraction:.2%}"
