"""Regression coverage for colour-enabled production display scenes."""

from __future__ import annotations

import pytest

from ralph.display.scene_catalog import SCENE_NAMES, SupportCase, render_scene


@pytest.mark.parametrize("background", ("dark", "light", "unknown"))
def test_display_regression_every_visible_scene_row_has_ansi_style(background: str) -> None:
    """S-3: colour-enabled scene rows must not silently fall back to terminal default."""
    case = SupportCase(background, "truecolour", "unicode", 100, "tty")
    unstyled_rows = [
        f"{scene_name}:{row_number}: {row!r}"
        for scene_name in SCENE_NAMES
        for row_number, row in enumerate(
            render_scene(
                scene_name,
                case,
                terminal_bg_is_light=case.terminal_background_is_light,
            ).splitlines(),
            start=1,
        )
        if row.strip() and "\x1b[" not in row
    ]

    assert not unstyled_rows, "Unstyled visible display rows:\n" + "\n".join(unstyled_rows)
