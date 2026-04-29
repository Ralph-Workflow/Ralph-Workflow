from __future__ import annotations

import importlib

from rich.console import Console

mode = importlib.import_module("ralph.display.mode")


def test_wide_console_returns_wide() -> None:
    console = Console(force_terminal=True, width=120)

    assert mode.detect_mode(console, {}) == "wide"


def test_narrow_console_returns_compact() -> None:
    console = Console(force_terminal=True, width=40)

    assert mode.detect_mode(console, {}) == "compact"


def test_threshold_boundary_returns_wide() -> None:
    # width == NARROW_THRESHOLD (60) is NOT < 60, so returns "wide"
    console = Console(force_terminal=True, width=mode.NARROW_THRESHOLD)

    assert mode.detect_mode(console, {}) == "wide"


def test_non_terminal_wide_returns_wide() -> None:
    console = Console(force_terminal=False, width=120)

    assert mode.detect_mode(console, {}) == "wide"


def test_ci_env_does_not_affect_mode() -> None:
    console = Console(force_terminal=True, width=120)

    assert mode.detect_mode(console, {"CI": "1"}) == "wide"


def test_no_color_env_does_not_affect_mode() -> None:
    console = Console(force_terminal=True, width=120)

    assert mode.detect_mode(console, {"NO_COLOR": "1"}) == "wide"


def test_ralph_force_narrow_returns_compact() -> None:
    console = Console(force_terminal=True, width=120)

    assert mode.detect_mode(console, {"RALPH_FORCE_NARROW": "1"}) == "compact"


def test_ralph_force_narrow_true_returns_compact() -> None:
    console = Console(force_terminal=True, width=200)

    assert mode.detect_mode(console, {"RALPH_FORCE_NARROW": "true"}) == "compact"
