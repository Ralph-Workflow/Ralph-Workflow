"""AC-C1 regression sweep for the black-on-black preview defect."""

from __future__ import annotations

import io
import re

import pytest
from rich.console import Console
from rich.text import Text

from ralph.display._tool_result_syntax import append_tool_result_syntax
from ralph.display.edit_preview import build_edit_preview, render_markdown_preview
from ralph.display.theme import contrast_ratio

_RGB_SGR = re.compile(r"\x1b\[38;2;(\d+);(\d+);(\d+)m")


def _render(renderable: object, *, color: bool) -> str:
    output = io.StringIO()
    Console(
        file=output,
        force_terminal=color,
        color_system="truecolor" if color else None,
        no_color=not color,
        width=80,
    ).print(renderable)
    return output.getvalue()


def _tool_result() -> Text:
    text = Text()
    assert append_tool_result_syntax(text, '{"result": 1}', "mcp__ralph__read_file", terminal_bg_is_light=None)
    return text


def _shapes(background: bool | None) -> dict[str, object]:
    common = {"width": 80, "terminal_bg_is_light": background}
    return {
        "write": build_edit_preview("write_file", {"path": "a.py", "content": "def f():\n return 1\n"}, **common),
        "edit": build_edit_preview("edit_file", {"path": "a.py", "edits": [{"oldText": "x = 1", "newText": "x = 2"}]}, **common),
        "read": build_edit_preview("read_file", {"path": "a.py", "content": '{"content":"x = 1","line_start":17}'}, **common),
        "multi": build_edit_preview("read_multiple_files", {"content": '{"files":[{"path":"a.py","content":"x = 1"}]}'}, **common),
        "grep": build_edit_preview("grep_files", {"content": '{"matches":[{"path":"a.py","line":17,"text":"needle = 1"}]}', "pattern": "needle"}, **common),
        "diff": build_edit_preview("git_diff", {"content": "@@ -1 +1 @@\n-old\n+new\n"}, **common),
        "markdown": render_markdown_preview("# Heading\n```python\nx = 1\n```", **common),
        "tool-result": _tool_result(),
    }


@pytest.mark.parametrize("terminal_bg_is_light", [False, True, None])
def test_preview_contrast_sweep_regression_no_black_on_black(terminal_bg_is_light: bool | None) -> None:
    """Every truecolour preview token clears 4.5:1 on its resolved background."""
    backgrounds = ("#FFFFFF",) if terminal_bg_is_light is True else (
        ("#000000",) if terminal_bg_is_light is False else ("#000000", "#FFFFFF")
    )
    for name, renderable in _shapes(terminal_bg_is_light).items():
        assert renderable is not None, name
        rendered = _render(renderable, color=True)
        assert "48;2;" not in rendered and "48;5;" not in rendered, name
        for red, green, blue in _RGB_SGR.findall(rendered):
            colour = f"#{int(red):02X}{int(green):02X}{int(blue):02X}"
            assert all(contrast_ratio(colour, background) >= 4.5 for background in backgrounds), (
                f"black-on-black regression in {name}: {colour} on {backgrounds}"
            )


def test_preview_color_stripped_output_preserves_structure() -> None:
    """AC-C6: titles, gutters, markers, and elision remain when all styles drop."""
    edit = _render(_shapes(None)["edit"], color=False)
    assert "-" in edit and "+" in edit and "1" in edit
    long = build_edit_preview(
        "write_file", {"path": "a.py", "content": "\n".join(str(i) for i in range(41))},
        width=80, terminal_bg_is_light=None,
    )
    assert long is not None
    assert "more line" in _render(long, color=False)
    assert "a.py" in _render(_shapes(None)["multi"], color=False)
