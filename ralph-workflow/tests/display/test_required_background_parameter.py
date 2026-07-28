"""S-1 prevents preview builders from silently choosing a palette."""

from __future__ import annotations

import inspect

from ralph.display.edit_preview import build_edit_preview, render_markdown_preview
from ralph.display.theme import identity_color


def test_s1_public_color_builders_require_resolved_background() -> None:
    for subject in (build_edit_preview, render_markdown_preview, identity_color):
        parameter = inspect.signature(subject).parameters["terminal_bg_is_light"]
        assert parameter.default is inspect.Parameter.empty
