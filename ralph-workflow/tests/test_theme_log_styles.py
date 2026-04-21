"""Tests for theme.log.* style entries in RALPH_THEME."""

from __future__ import annotations

import pytest

from ralph.display.theme import RALPH_THEME


@pytest.mark.parametrize(
    "name",
    [
        "theme.log.info",
        "theme.log.success",
        "theme.log.warn",
        "theme.log.error",
        "theme.log.milestone",
    ],
)
def test_theme_log_style_registered(name: str) -> None:
    assert name in RALPH_THEME.styles
    assert str(RALPH_THEME.styles[name]) != ""
