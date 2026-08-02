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

import io

import pytest

import ralph.display._terminal_bg_query as terminal_query
import ralph.display.theme as theme_module
from ralph.display._terminal_bg_query import parse_osc11_reply
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.theme import (
    SYNTAX_THEME_ON_DARK_BG,
    SYNTAX_THEME_ON_LIGHT_BG,
    SYNTAX_THEME_ON_UNKNOWN_BG,
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


def test_display_context_regression_shares_background_with_parallel_display() -> None:
    """S-1: every display surface receives the context's single resolution."""
    context = make_display_context(env={"RALPH_TERMINAL_BG": "dark"})
    display = ParallelDisplay(context)
    assert display._terminal_bg_is_light is context.terminal_background_is_light


def test_syntax_theme_tracks_the_resolved_background() -> None:
    """Each resolved background chooses its contrast-safe fixed-RGB palette."""
    assert syntax_theme_for_background(True) == SYNTAX_THEME_ON_LIGHT_BG
    assert syntax_theme_for_background(False) == SYNTAX_THEME_ON_DARK_BG
    assert syntax_theme_for_background(None) == SYNTAX_THEME_ON_UNKNOWN_BG


def test_terminal_background_regression_uses_dev_tty_when_stdin_is_redirected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-4: a tty stdout opens /dev/tty when stdin is redirected."""
    class _Stream(io.StringIO):
        def isatty(self) -> bool:
            return self is stdout

    stdin = _Stream()
    stdout = _Stream()
    opened: list[tuple[str, int]] = []
    monkeypatch.setattr(terminal_query.sys, "stdin", stdin)
    monkeypatch.setattr(terminal_query.sys, "stdout", stdout)
    monkeypatch.setattr(
        terminal_query.os,
        "open",
        lambda path, flags: opened.append((path, flags)) or 77,
    )
    assert terminal_query._tty_fd() == (77, True)
    assert opened == [("/dev/tty", terminal_query.os.O_RDWR | terminal_query.os.O_NOCTTY)]


def test_terminal_background_regression_restores_tty_after_probe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-4: a failed raw-mode exchange restores the terminal and closes /dev/tty."""
    monkeypatch.setattr(terminal_query, "_tty_fd", lambda: (77, True))
    monkeypatch.setattr(terminal_query.sys, "platform", "linux")
    restored: list[tuple[int, int, list[int]]] = []
    closed: list[int] = []

    class _Termios:
        TCSANOW = 0
        TCSADRAIN = 1

        @staticmethod
        def tcgetattr(_fd: int) -> list[int]:
            return [1]

        @staticmethod
        def tcsetattr(fd: int, when: int, original: list[int]) -> None:
            restored.append((fd, when, original))

    class _Tty:
        @staticmethod
        def setraw(_fd: int, _when: int) -> None:
            raise OSError("raw mode failed")

    monkeypatch.setitem(__import__("sys").modules, "termios", _Termios)
    monkeypatch.setitem(__import__("sys").modules, "tty", _Tty)
    monkeypatch.setattr(terminal_query.os, "close", closed.append)
    assert terminal_query._probe(0.1) == (True, None)
    assert restored == [(77, 1, [1])]
    assert closed == [77]


def test_terminal_background_regression_no_tty_does_not_memoize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-4: an unavailable tty remains retryable instead of caching None."""
    terminal_query.reset_cache()
    monkeypatch.setattr(terminal_query, "_probe", lambda _timeout: (False, None))
    assert terminal_query.query_terminal_background_hex() is None
    assert terminal_query._probed is False


def test_terminal_background_regression_timeout_env_reaches_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-4: the documented millisecond override controls the OSC 11 deadline."""
    observed: list[float] = []
    monkeypatch.setattr(
        terminal_query,
        "query_terminal_background_hex",
        lambda *, timeout: observed.append(timeout) or "#000000",
    )
    assert theme_module.detect_terminal_background_is_light({"RALPH_TERMINAL_BG_TIMEOUT_MS": "250"}) is False
    assert observed == [0.25]
