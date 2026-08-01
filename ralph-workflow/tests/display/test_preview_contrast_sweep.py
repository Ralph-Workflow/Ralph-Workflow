"""AC-C1 regression sweep for the black-on-black preview defect."""

from __future__ import annotations

import io
import re

import pytest
from rich.console import Console

from ralph.display.edit_preview import build_edit_preview, render_markdown_preview
from ralph.display.theme import (
    contrast_ratio,
    diff_fill_styles,
    diff_token_foregrounds,
    pick_status_styles,
    preview_background_for_background,
)

_RGB_SGR = re.compile(r"38;2;(\d+);(\d+);(\d+)")
_SGR = re.compile(r"\x1b\[([0-9;]*)m")


def _assert_no_operator_palette_sgr(rendered: str, name: str) -> None:
    """Reject ANSI-slot colours and fills that cannot prove terminal contrast."""
    for match in _SGR.finditer(rendered):
        parameters = [int(value) for value in match.group(1).split(";") if value]
        operator_slots: list[int] = []
        index = 0
        while index < len(parameters):
            parameter = parameters[index]
            if (
                parameter in {38, 48}
                and index + 2 < len(parameters)
                and parameters[index + 1] in {2, 5}
            ):
                index += 5 if parameters[index + 1] == 2 else 3
                continue
            operator_slots.append(parameter)
            index += 1
        assert not any(30 <= value <= 37 or 90 <= value <= 97 for value in operator_slots), (
            f"operator-palette foreground in {name}: {match.group(0)!r}"
        )
        assert not any(40 <= value <= 47 or 100 <= value <= 107 for value in operator_slots), (
            f"underived background fill in {name}: {match.group(0)!r}"
        )


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


def _shapes(background: bool | None) -> dict[str, object]:
    common = {"width": 80, "terminal_bg_is_light": background}
    return {
        "write": build_edit_preview(
            "write_file", {"path": "a.py", "content": "def f():\n return 1\n"}, **common
        ),
        "edit": build_edit_preview(
            "edit_file",
            {"path": "a.py", "edits": [{"oldText": "x = 1", "newText": "x = 2"}]},
            **common,
        ),
        "read": build_edit_preview(
            "read_file",
            {"path": "a.py", "content": '{"content":"x = 1","line_start":17}'},
            **common,
        ),
        "multi": build_edit_preview(
            "read_multiple_files",
            {"content": '{"files":[{"path":"a.py","content":"x = 1"}]}'},
            **common,
        ),
        "grep": build_edit_preview(
            "grep_files",
            {
                "content": '{"matches":[{"path":"a.py","line":17,"text":"needle = 1"}]}',
                "pattern": "needle",
            },
            **common,
        ),
        "diff": build_edit_preview("git_diff", {"content": "@@ -1 +1 @@\n-old\n+new\n"}, **common),
        "edit_painted": build_edit_preview(
            "edit_file",
            {"path": "a.py", "edits": [{"oldText": "x = 1", "newText": "x = 2"}]},
            diff_fills=diff_fill_styles(background),
            **common,
        ),
        "diff_painted": build_edit_preview(
            "git_diff",
            {"content": "@@ -1 +1 @@\n-old\n+new\n"},
            diff_fills=diff_fill_styles(background),
            **common,
        ),
        "markdown": render_markdown_preview(
            "# Heading\n\n## Subheading\n\n*emphasis* and **strong** and `inline code`.\n\n"
            "> block quote\n\n---\n\n| left | right |\n| --- | --- |\n| one | two |\n\n"
            "- bullet\n1. one\n2. two\n\nPress <kbd>Ctrl</kbd>.\n\n"
            "[link](https://example.com)\n\n```python\nx = 1\n```",
            **common,
        ),
        "tool-result": build_edit_preview(
            "read_file", {"path": "result.json", "content": '{"result": 1}'}, **common
        ),
    }


@pytest.mark.parametrize("terminal_bg_is_light", [False, True, None])
def test_preview_contrast_sweep_regression_no_black_on_black(
    terminal_bg_is_light: bool | None,
) -> None:
    """Every truecolour preview token clears 4.5:1 on its resolved background."""
    backgrounds = (
        ("#FFFFFF",)
        if terminal_bg_is_light is True
        else (("#000000",) if terminal_bg_is_light is False else ("#000000", "#FFFFFF"))
    )
    for name, renderable in _shapes(terminal_bg_is_light).items():
        assert renderable is not None, name
        rendered = _render(renderable, color=True)
        fills = diff_fill_styles(terminal_bg_is_light) if name.endswith("_painted") else None
        surface = preview_background_for_background(terminal_bg_is_light)
        if fills is None and surface == "default":
            assert "48;2;" not in rendered and "48;5;" not in rendered, name
        elif fills is None:
            surface_sgr = f"48;2;{int(surface[1:3], 16)};{int(surface[3:5], 16)};{int(surface[5:7], 16)}"
            assert surface_sgr in rendered, name
        else:
            fill_sgrs = {
                f"48;2;{int(fill[1:3], 16)};{int(fill[3:5], 16)};{int(fill[5:7], 16)}"
                for fill in fills
            }
            assert all(sgr in rendered for sgr in fill_sgrs), name
            markers = pick_status_styles(terminal_bg_is_light)
            for color in (*diff_token_foregrounds(terminal_bg_is_light), *[
                re.search(r"#[0-9A-Fa-f]{6}", markers[status][0]).group()
                for status in ("error", "success")
            ]):
                assert all(contrast_ratio(color, fill) >= 4.5 for fill in fills)
        _assert_no_operator_palette_sgr(rendered, name)
        colors = _RGB_SGR.findall(rendered)
        assert colors, f"contrast sweep emitted no truecolour tokens for {name}"
        # Rich emits implementation-owned gutter and padding styles alongside
        # Pygments tokens. Token palettes are checked at their public theme
        # seam; here the generated rendering proves that the complete owned
        # surface reaches the terminal without ANSI-slot fallbacks.
        if surface == "default":
            for red, green, blue in colors:
                colour = f"#{int(red):02X}{int(green):02X}{int(blue):02X}"
                assert all(contrast_ratio(colour, background) >= 4.5 for background in backgrounds), (
                    f"black-on-black regression in {name}: {colour} on {backgrounds}"
                )


def test_preview_color_stripped_output_preserves_structure() -> None:
    """AC-C6: titles, gutters, markers, and elision remain when all styles drop."""
    edit = _render(_shapes(None)["edit"], color=False)
    assert "-" in edit and "+" in edit and "1" in edit
    long = build_edit_preview(
        "write_file",
        {"path": "a.py", "content": "\n".join(str(i) for i in range(41))},
        width=80,
        terminal_bg_is_light=None,
    )
    assert long is not None
    assert "more line" in _render(long, color=False)
    assert "a.py" in _render(_shapes(None)["multi"], color=False)
