"""Tests for measured terminal-background resolution and theme selection.

The syntax-highlight colours must be chosen against the operator's ACTUAL
terminal background, which can be any colour -- not just black or white.
This suite pins the three pieces that make that true:

  1. ``parse_osc11_reply`` understands the OSC 11 replies real emulators
     send (1/2/4 hex digits per channel, BEL- or ST-terminated) and
     rejects junk.
  2. ``background_hex_is_light`` classifies arbitrary colours by measured
     WCAG luminance at the crossover point, so mid-tone and themed
     backgrounds land on the side that actually reads better.
  3. ``terminal_background_is_light`` honours the documented precedence
     (explicit override > measured colour > COLORFGBG) and
     ``syntax_theme_for_background`` maps the result onto an ANSI theme,
     never a fixed-RGB one.

Each test is pure in-process string/number work; the whole file runs far
under the per-file budget.
"""

from __future__ import annotations

import pytest

from ralph.display._terminal_bg_query import parse_osc11_reply
from ralph.display.theme import (
    SYNTAX_THEME_ON_DARK_BG,
    SYNTAX_THEME_ON_LIGHT_BG,
    background_hex_is_light,
    syntax_theme_for_background,
    terminal_background_is_light,
)

# ---------------------------------------------------------------------------
# 1. OSC 11 reply parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        # 4-hex-digit form, BEL terminated (xterm, iTerm2).
        ("\x1b]11;rgb:fdfd/f6f6/e3e3\x07", "#FDF6E3"),
        # 2-hex-digit form, ST terminated (kitty, foot).
        ("\x1b]11;rgb:28/2a/36\x1b\\", "#282A36"),
        # 1-hex-digit form: the digit is full-scale for its width.
        ("\x1b]11;rgb:0/0/0\x07", "#000000"),
        ("\x1b]11;rgb:f/f/f\x07", "#FFFFFF"),
        # Leading noise (a stray keypress echo) does not defeat the search.
        ("junk\x1b]11;rgb:1c1c/1c1c/1c1c\x07", "#1C1C1C"),
    ],
)
def test_parse_osc11_reply_accepts_real_terminal_replies(reply: str, expected: str) -> None:
    """Every per-channel width real emulators emit parses to #RRGGBB."""
    assert parse_osc11_reply(reply) == expected


@pytest.mark.parametrize("reply", ["", "\x1b]11;?\x07", "no colour here", "rgb:zz/zz/zz"])
def test_parse_osc11_reply_rejects_non_replies(reply: str) -> None:
    """A timeout, an echo of our own query, or junk yields None -- not a guess."""
    assert parse_osc11_reply(reply) is None


# ---------------------------------------------------------------------------
# 2. Arbitrary background colours are classified by measured luminance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bg_hex", "is_light"),
    [
        ("#000000", False),  # pure black
        ("#FFFFFF", True),  # pure white
        ("#FDF6E3", True),  # Solarized Light base3
        ("#002B36", False),  # Solarized Dark base03
        ("#282828", False),  # Gruvbox dark bg
        ("#FBF1C7", True),  # Gruvbox light bg
        ("#282A36", False),  # Dracula bg
        ("#2E3440", False),  # Nord polar night
        ("#ECEFF4", True),  # Nord snow storm
        ("#3D3D3D", False),  # mid-dark slate
        ("#B0B0B0", True),  # mid-light grey
    ],
)
def test_background_hex_is_light_classifies_arbitrary_theme_backgrounds(
    bg_hex: str, is_light: bool
) -> None:
    """Real theme backgrounds -- none of them black or white -- classify correctly."""
    assert background_hex_is_light(bg_hex) is is_light


def test_background_hex_is_light_returns_none_for_unparseable() -> None:
    """A malformed colour is undetermined, not silently classified."""
    assert background_hex_is_light("not-a-colour") is None
    assert background_hex_is_light("#12345") is None


def test_background_hex_is_light_uses_contrast_crossover_not_midpoint() -> None:
    """A mid-grey background sits ABOVE the crossover, so dark text wins there.

    A naive "is it brighter than 50% grey" test would call #808080 dark;
    WCAG's crossover puts it on the light side, which is what actually
    reads better. This pins the measured behaviour against that regression.
    """
    assert background_hex_is_light("#808080") is True
    assert background_hex_is_light("#595959") is False


# ---------------------------------------------------------------------------
# 3. Precedence and theme mapping
# ---------------------------------------------------------------------------


def test_explicit_env_override_beats_measured_colour() -> None:
    """An operator who names the background wins over the measurement."""
    assert (
        terminal_background_is_light({"RALPH_TERMINAL_BG": "dark"}, measured_bg_hex="#FFFFFF")
        is False
    )
    assert (
        terminal_background_is_light({"RALPH_TERMINAL_BG": "light"}, measured_bg_hex="#000000")
        is True
    )


def test_env_override_accepts_a_hex_colour() -> None:
    """RALPH_TERMINAL_BG may name the colour itself, not just light/dark."""
    assert terminal_background_is_light({"RALPH_TERMINAL_BG": "#FDF6E3"}) is True
    assert terminal_background_is_light({"RALPH_TERMINAL_BG": "#002B36"}) is False


def test_measured_colour_beats_colorfgbg_heuristic() -> None:
    """A measurement outranks the coarse legacy hint when they disagree."""
    env = {"COLORFGBG": "15;0"}  # claims a black background
    assert terminal_background_is_light(env, measured_bg_hex="#FDF6E3") is True


def test_colorfgbg_used_only_when_nothing_was_measured() -> None:
    """Without a measurement the legacy hint still applies."""
    assert terminal_background_is_light({"COLORFGBG": "0;15"}) is True
    assert terminal_background_is_light({"COLORFGBG": "15;0"}) is False


def test_no_signal_is_undetermined() -> None:
    """No override, no measurement, no hint -> None, so the caller defaults."""
    assert terminal_background_is_light({}) is None


def test_syntax_theme_is_background_specific_pygments_theme() -> None:
    """Both themes are fixed token palettes chosen for the resolved background."""
    assert syntax_theme_for_background(False) is SYNTAX_THEME_ON_DARK_BG
    assert syntax_theme_for_background(True) is SYNTAX_THEME_ON_LIGHT_BG


def test_syntax_theme_tracks_the_resolved_background() -> None:
    """Light backgrounds get the normal ANSI slots; dark and unknown get bright."""
    assert syntax_theme_for_background(True) == SYNTAX_THEME_ON_LIGHT_BG
    assert syntax_theme_for_background(False) == SYNTAX_THEME_ON_DARK_BG
    assert syntax_theme_for_background(None) == SYNTAX_THEME_ON_DARK_BG
