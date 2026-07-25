"""Line sanitization for safe display in terminal UI.

Handles oversize lines (truncated at max_chars with '…' suffix), binary bytes
(decoded with errors='replace'), CRLF normalization, control character stripping
(tab preserved), emoji preservation, and full CSI / OSC / two-character ESC
escape containment (alternate screen, erase display, cursor positioning,
scroll region, cursor hide/show, private-parameter CSI like ``ESC[>0c`` and
``ESC[<35;1;2M``, OSC titles, two-character ESC forms like ``ESC M``).

The stripper uses the repository-proven full CSI parameter-byte range taken
from :mod:`ralph.display.vt_normalizer` so every valid CSI sequence is
matched (including the private-parameter forms that a narrower digit-only
class leaks). The narrower forms used elsewhere in the display module are
NOT safe at the agent-content boundary and must never be copied here.
"""

from __future__ import annotations

import re

from rich.text import Text

# Full CSI+OSC+two-char ESC stripper: takes the [0-?] parameter byte range
# (0x30-0x3F: digits plus ':', ';', '<', '=', '>', '?') so it matches every
# valid CSI sequence including private-parameter forms.
_TERMINAL_ESCAPE_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]"
    r"|\][^\x1b\x07]*(?:\x07|\x1b\\)"
    r"|[@-Z\\-_])"
)

# Bare C0 controls: spares TAB (0x09) and LF (0x0a) -- callers rely on
# line structure and tab alignment. Deletes every other C0 control byte
# (0x00-0x08, 0x0b-0x1f) plus DEL (0x7f).
_C0_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def strip_terminal_control(text: str) -> str:
    """Remove every terminal control construct from ``text`` while preserving visible characters.

    Strips, in order:

    - **CSI** sequences (``ESC[`` followed by parameter bytes in
      ``0x30-0x3F`` and intermediate bytes in ``0x20-0x2F`` and a final
      byte in ``0x40-0x7E``) -- covers alternate screen
      (``ESC[?1049h``/``ESC[?1049l``), erase display (``ESC[2J``) and
      line (``ESC[K``), cursor positioning (``ESC[H``,
      ``ESC[12;40H``), scroll region (``ESC[1;50r``), cursor hide/show
      (``ESC[?25l``/``ESC[?25h``), SGR colour (``ESC[32m``/``ESC[0m``),
      and the private-parameter forms ``ESC[>0c`` (device attributes
      reply) and ``ESC[<35;1;2M`` (SGR mouse report).

    - **OSC** sequences (``ESC]`` ... terminator) -- titles
      (``ESC]0;some title BEL`` and the ST-terminated
      ``ESC]0;t ESC\\`` form).

    - **Two-character ESC** forms (``ESC M`` reverse index, etc.).

    Then sweeps any orphaned C0 control bytes (Bell, Unit-Separator,
    DEL, etc.) while sparing TAB and LF so line structure and tab
    alignment survive.

    Args:
        text: Input string. May contain CSI/OSC/C0/escape bytes.

    Returns:
        A new string with every terminal control construct removed.
        Visible characters (including TAB and LF) are preserved
        unchanged. ``""`` is returned when ``text`` is empty.
    """
    if not text:
        return ""
    # Strip complete sequences first so the body vanishes with the ESC.
    cleaned = _TERMINAL_ESCAPE_RE.sub("", text)
    # Sweep any orphaned C0 bytes that may have escaped the regex.
    cleaned = _C0_CONTROL_RE.sub("", cleaned)
    return cleaned


def strip_markup_safe(text: str) -> str:
    """Reduce Rich markup to plain text and strip terminal control, never raising.

    This is the ONLY place in the package allowed to hand agent-origin text
    to :meth:`rich.text.Text.from_markup`; every display sink routes through
    it (the audit's package-wide ``from_markup`` invariant enforces that).

    ``from_markup`` is a *parser*, and arbitrary agent output is adversarial
    input to it. A grep whose pattern lists PDF filter names emits
    ``[/pdf /text /imageb]``, which Rich reads as a closing tag with no open
    tag and rejects with ``MarkupError``. Rich raises from its own
    ``ConsoleError`` hierarchy (``MarkupError``, ``StyleSyntaxError``) --
    NOT ``ValueError`` -- so a guard naming a specific exception type has
    already failed once here, and a new Rich release may add another type.
    A display sink that raises takes the whole run down with it, so the
    guard is deliberately total: any parse failure falls back to the
    literal text, which stays copy-pasteable.

    Terminal control bytes are stripped on BOTH paths -- the fallback must
    not become an escape-containment hole.

    Args:
        text: Arbitrary text, possibly carrying Rich markup, malformed
            bracket sequences, and terminal control bytes.

    Returns:
        Plain text with valid markup reduced, malformed markup left
        literal, and every terminal control construct removed.
    """
    if not text:
        return ""
    try:
        plain = Text.from_markup(text).plain
    except Exception:  # a display sink must never raise -- see docstring
        plain = text
    return strip_terminal_control(plain)


def sanitize_display_line(raw: bytes | str, max_chars: int = 200) -> str:
    """Sanitize a raw agent output line for safe terminal display.

    Decodes bytes (errors='replace'), normalizes CRLF to LF, removes every
    terminal control construct via :func:`strip_terminal_control` (so the
    output is safe to print on a real TTY -- no alternate-screen swaps,
    erase-display repaints, cursor moves, or private-parameter CSI leaks),
    and truncates to ``max_chars`` with a '…' suffix when over the limit.
    TAB and LF are preserved so callers can rely on line structure and
    tab alignment.
    """
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = strip_terminal_control(text)

    if len(text) > max_chars:
        text = text[:max_chars] + "…"

    return text


__all__ = ["sanitize_display_line", "strip_markup_safe", "strip_terminal_control"]
