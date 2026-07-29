"""Containment tests for Rich markup PARSING at the display boundary.

Sibling of ``test_terminal_escape_containment.py``: that file pins what the
display sinks must STRIP, this one pins that they must never RAISE.

The bug: ``rich.text.Text.from_markup`` is a parser, and agent output is
adversarial input to it. A developer agent ran a grep whose pattern listed
PDF filter names, so ``[/pdf /text /imageb /imagec /imagei]`` reached an
activity line. Rich reads ``[/...]`` as a closing tag, finds no open tag,
and raises ``MarkupError`` -- which inherits from ``ConsoleError``, NOT from
``ValueError``. Both sinks guarded with ``except ValueError``, so the
exception propagated out of ``ParallelDisplay.emit_parsed_event`` and killed
the run.

The fix has two layers, and this file tests both:

  1. :func:`ralph.display.line_sanitizer.strip_markup_safe` is the single
     guarded parse site, and its guard is TOTAL -- no exception type can
     slip past it, including whatever a future Rich release adds.
  2. The terminal-escape-containment audit forbids any other
     ``from_markup`` call on non-literal text anywhere under ``ralph/``,
     so a new sink cannot re-open the hole by writing its own guard.

No real subprocess, no time.sleep, no wall-clock waits (audit_test_policy
forbids them).
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

import ralph.testing.audit_terminal_escape_containment as audit_module
from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.activity_model import render_event_line
from ralph.display.context import DisplayContext, make_display_context
from ralph.display.line_sanitizer import strip_markup_safe
from ralph.display.parallel_display import ParallelDisplay, strip_markup
from ralph.testing.audit_terminal_escape_containment import main as audit_main

if TYPE_CHECKING:
    from collections.abc import Callable

# The exact payload from the production traceback (truncated to the part
# that matters -- the bracket group Rich rejects).
PRODUCTION_CRASH_LINE = (
    "Command: env RAILS_ENV=test bin/rails test --profile=20 2>&1 | "
    "grep -B 2 [/pdf /text /imageb /imagec /imagei]"
)

# Bracket shapes Rich rejects, each a real class of agent output:
#   - unmatched explicit close (the production crash)
#   - unmatched implicit close
#   - mismatched open/close pair
#   - close with no open, mid-line
#   - regex character classes and path globs that read as tags
MALFORMED_MARKUP_CORPUS: tuple[str, ...] = (
    PRODUCTION_CRASH_LINE,
    "[/pdf /text /imageb /imagec /imagei]",
    "[/]",
    "[/close]",
    "[a][/b]",
    "grep -E '[/usr/bin|/usr/local]' file.txt",
    "sed 's[/foo[/bar[/g'",
    "diff: [/etc/hosts] vs [/etc/hosts.bak]",
    "traceback: File [/app/main.py], line 3",
    "[[/x]]",
    "[/x][/y][/z]",
)

# Bracket-heavy text Rich accepts as VALID markup -- these must keep
# working (the sinks reduce them), so the fix cannot be "stop parsing".
VALID_MARKUP_CASES: tuple[tuple[str, str], ...] = (
    ("[green]ok[/green]", "ok"),
    ("[bold]a[/bold] and [dim]b[/dim]", "a and b"),
    # Rich AUTO-CLOSES an unclosed open tag -- this is valid markup, not
    # malformed input, so the sinks reduce it rather than keeping it literal.
    ("[bold]never closed", "never closed"),
    ("plain text", "plain text"),
    ("", ""),
)

# Every sink that must survive the corpus, named for the failure message.
# The third sink is ``strip_markup_safe`` itself: ``_plain_constants._sanitize``
# is a thin alias for the same choke point, so re-testing the choke point
# here would duplicate the parameterized case at no extra signal. We
# instead exercise ``parallel_display.strip_markup`` which goes through
# its own (now-deleted-private) path and gives a different sink surface.
_MARKUP_SINKS: tuple[tuple[str, Callable[[str], str]], ...] = (
    ("strip_markup_safe", strip_markup_safe),
    ("parallel_display.strip_markup", strip_markup),
)

_SINK_IDS: tuple[str, ...] = tuple(label for label, _sink in _MARKUP_SINKS)

HOSTILE_PREFIX = "\x1b[?1049h\x1b[2J\x1b[>0c"


def _make_parallel_display() -> tuple[ParallelDisplay, DisplayContext, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=120)
    ctx = make_display_context(console=console, env={"CI": "1"})
    return ParallelDisplay(ctx, is_quiet=False), ctx, buf


# ---------------------------------------------------------------------------
# Layer 1: the choke point and every sink that delegates to it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("line", MALFORMED_MARKUP_CORPUS)
@pytest.mark.parametrize(("sink_label", "sink"), _MARKUP_SINKS, ids=_SINK_IDS)
def test_sink_keeps_malformed_markup_literal(
    sink_label: str, sink: Callable[[str], str], line: str
) -> None:
    """Every markup sink returns malformed input verbatim instead of raising."""
    assert sink(line) == line, (
        f"{sink_label} must return malformed markup unchanged; got {sink(line)!r}"
    )


@pytest.mark.parametrize(("markup", "expected"), VALID_MARKUP_CASES)
@pytest.mark.parametrize(("sink_label", "sink"), _MARKUP_SINKS, ids=_SINK_IDS)
def test_sink_still_reduces_valid_markup(
    sink_label: str, sink: Callable[[str], str], markup: str, expected: str
) -> None:
    """The guard must not be paid for by giving up markup reduction."""
    assert sink(markup) == expected, f"{sink_label} broke valid-markup reduction for {markup!r}"


@pytest.mark.parametrize("line", MALFORMED_MARKUP_CORPUS)
@pytest.mark.parametrize(("sink_label", "sink"), _MARKUP_SINKS, ids=_SINK_IDS)
def test_sink_strips_control_bytes_on_the_fallback_path(
    sink_label: str, sink: Callable[[str], str], line: str
) -> None:
    """The malformed-markup fallback must not become an escape-containment hole.

    The parse fails, so the fallback returns the literal text -- it MUST
    still run the terminal-control stripper on it.
    """
    result = sink(HOSTILE_PREFIX + line)
    assert "\x1b" not in result, f"{sink_label}: ESC byte leaked on the fallback path: {result!r}"
    assert result == line, f"{sink_label}: fallback mangled the visible text: {result!r}"


def test_strip_markup_safe_survives_arbitrary_bracket_permutations() -> None:
    """Brute-force sweep: no arrangement of bracket tokens may raise.

    Enumerates every 3-token permutation of the bracket shapes that appear
    in real agent output (open, close, empty close, nested, regex class).
    Rich's parser is the component under test -- the sweep exists so a Rich
    upgrade that adds a new rejection case is caught here rather than in a
    live run.
    """
    tokens = ("[a]", "[/a]", "[/]", "[/x y]", "[[", "]]", "[0-9]", "[/usr/bin]", "text")
    for first in tokens:
        for second in tokens:
            for third in tokens:
                line = f"{first}{second}{third}"
                result = strip_markup_safe(line)
                assert isinstance(result, str), f"strip_markup_safe returned non-str for {line!r}"
                assert "\x1b" not in result


def test_strip_markup_safe_is_total_over_non_markup_exception_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard must catch exception types nobody has enumerated yet.

    A future Rich release may raise a type outside ``ConsoleError``. The
    regression that caused this bug was naming types in the guard, so the
    contract is a TOTAL guard -- proven here by making the parser raise a
    type unrelated to markup.
    """
    import ralph.display.line_sanitizer as sanitizer_module

    class _UnrelatedRichError(RuntimeError):
        """Stands in for an exception type a future Rich version may raise."""

    class _ExplodingText:
        @staticmethod
        def from_markup(_text: str) -> object:
            raise _UnrelatedRichError("rich changed its exception hierarchy")

    monkeypatch.setattr(sanitizer_module, "Text", _ExplodingText)

    assert sanitizer_module.strip_markup_safe("anything at all") == "anything at all"
    assert sanitizer_module.strip_markup_safe(HOSTILE_PREFIX + "boom") == "boom"


# ---------------------------------------------------------------------------
# Layer 1b: the full emit path from the production traceback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", list(ActivityEventKind))
def test_emit_parsed_event_survives_malformed_markup_for_every_kind(
    kind: ActivityEventKind,
) -> None:
    """``emit_parsed_event`` is where the exception escaped in production.

    Drives the exact crashing payload through every event kind, because the
    kind selects the downstream renderer and only one of them was exercised
    by the crashing run.
    """
    display, _ctx, buf = _make_parallel_display()
    metadata: dict[str, object] = {"type": "text", "text": PRODUCTION_CRASH_LINE}

    display.emit_parsed_event("unit-1", kind, PRODUCTION_CRASH_LINE, metadata)

    assert "\x1b" not in buf.getvalue()


@pytest.mark.parametrize("line", MALFORMED_MARKUP_CORPUS)
def test_emit_parsed_event_survives_the_whole_corpus(line: str) -> None:
    """The corpus goes through the real emit path, not just the leaf helper."""
    display, _ctx, buf = _make_parallel_display()

    display.emit_parsed_event("unit-1", ActivityEventKind.TEXT, line, {"text": line})

    assert "\x1b" not in buf.getvalue()


@pytest.mark.parametrize("line", MALFORMED_MARKUP_CORPUS)
def test_render_event_line_survives_malformed_markup(line: str) -> None:
    """The activity_router render path must not raise on the corpus either."""
    rendered = render_event_line(ActivityEventKind.TEXT, line)

    assert isinstance(rendered, str)
    assert "\x1b" not in rendered


# ---------------------------------------------------------------------------
# Layer 2: the audit that stops a new sink from re-opening the hole
# ---------------------------------------------------------------------------


def test_markup_parse_invariant_is_clean_on_the_real_tree() -> None:
    """No unguarded ``from_markup`` call exists under ``ralph/`` today."""
    invariant = audit_module.MarkupParseInvariant()

    assert invariant.violations() == []


def test_markup_parse_allowlist_contents_are_pinned() -> None:
    """Adding an allowlist entry is a contract change and must land here too.

    Each allowlisted file is either the guarded choke point itself or a file
    whose markup arguments are author-written literals. A new entry means a
    new file is permitted to parse markup unguarded -- that decision belongs
    in review, not in a quiet dict edit.
    """
    assert set(audit_module._MARKUP_PARSE_ALLOWLIST) == {
        "display/line_sanitizer.py",
        "cli/commands/contribute.py",
    }


@pytest.mark.timeout_seconds(2)
def test_audit_blocks_a_new_unguarded_from_markup_call_site(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Adversarial: a new sink parses agent text directly instead of delegating."""
    path = "display/activity_model.py"

    def _transform(src: str) -> str:
        return src + "\n\ndef _new_sink(agent_text):\n    return Text.from_markup(agent_text)\n"

    _patch_rel(monkeypatch, path, _transform)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1, "audit must exit 1 for an unguarded from_markup call on non-literal text"
    assert "unguarded from_markup() on non-literal text" in captured.out
    assert path in captured.out


def test_audit_allows_from_markup_on_an_author_written_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A string-literal argument is provably not agent-origin, so it stays legal.

    Without this the audit would push authors to route static banner markup
    through the stripper, which would render their markup as literal text.
    """
    path = "display/activity_model.py"

    def _transform(src: str) -> str:
        return src + '\n\ndef _banner():\n    return Text.from_markup("[bold]hi[/bold]")\n'

    _patch_rel(monkeypatch, path, _transform)

    assert audit_module.MarkupParseInvariant().violations() == []


def test_audit_blocks_reverting_the_choke_point_guard_to_valueerror(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Adversarial: re-introduce the exact regression -- a type-named guard.

    ``except ValueError`` looks plausible and passes every functional test
    that does not happen to feed malformed markup, so the audit pins the
    total guard by literal.
    """
    path = "display/line_sanitizer.py"

    def _transform(src: str) -> str:
        return src.replace(
            "    except Exception:  # a display sink must never raise -- see docstring",
            "    except ValueError:",
        )

    _patch_rel(monkeypatch, path, _transform)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1, "audit must exit 1 when the choke-point guard is narrowed to a named type"
    assert "strip_markup_safe body" in captured.out


def test_audit_blocks_a_sink_that_stops_delegating_to_the_choke_point(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Adversarial: ``_strip_markup`` goes back to parsing markup itself."""
    path = "display/parallel_display.py"

    def _transform(src: str) -> str:
        return src.replace(
            "    return strip_markup_safe(line)",
            "    return strip_terminal_control(Text.from_markup(line).plain)",
        )

    _patch_rel(monkeypatch, path, _transform)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1, "audit must exit 1 when a sink re-implements markup parsing"
    assert "_strip_markup body" in captured.out


def _patch_rel(
    monkeypatch: pytest.MonkeyPatch, rel_path: str, transform: Callable[[str], str]
) -> None:
    """Serve a transformed source for one package file to every audit invariant."""
    real_read = audit_module._read

    def _read(rel_path_arg: str) -> str:
        content = real_read(rel_path_arg)
        if rel_path_arg == rel_path:
            return transform(content)
        return content

    monkeypatch.setattr(audit_module, "_read", _read)
