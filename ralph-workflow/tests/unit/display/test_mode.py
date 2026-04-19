from __future__ import annotations

import importlib

from rich.console import Console

mode = importlib.import_module("ralph.display.mode")


def test_no_color_wins_over_force_color() -> None:
    console = Console(force_terminal=True, width=120)

    assert mode.detect_mode(console, {"NO_COLOR": "1", "FORCE_COLOR": "1"}) == "lines"


def test_ci_forces_lines() -> None:
    console = Console(force_terminal=True, width=120)

    assert mode.detect_mode(console, {"CI": "1"}) == "lines"


def test_term_dumb_forces_lines() -> None:
    console = Console(force_terminal=True, width=120)

    assert mode.detect_mode(console, {"TERM": "dumb"}) == "lines"


def test_non_terminal_forces_lines() -> None:
    console = Console(force_terminal=False, width=120)

    assert mode.detect_mode(console, {}) == "lines"


def test_narrow_terminal_forces_lines() -> None:
    console = Console(force_terminal=True, width=40)

    assert mode.detect_mode(console, {}) == "lines"


def test_dashboard_when_tty_and_wide() -> None:
    console = Console(force_terminal=True, width=120)

    assert mode.detect_mode(console, {}) == "dashboard"


def test_force_color_enables_dashboard_without_tty() -> None:
    console = Console(force_terminal=False, width=120)

    assert mode.detect_mode(console, {"FORCE_COLOR": "1"}) == "dashboard"


def test_non_terminal_without_force_color_stays_lines() -> None:
    console = Console(force_terminal=False, width=120)

    assert mode.detect_mode(console, {}) == "lines"
