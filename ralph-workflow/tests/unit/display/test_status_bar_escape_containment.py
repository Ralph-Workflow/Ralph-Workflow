"""Regression tests for terminal-escape containment at Status Bar sinks.

Every assertion in this file is made against a PUBLIC surface:

* the Status Bar layout is exercised through
  :func:`ralph.display.render_status_bar` (the pure, public renderer), and
* the persisted record is exercised through
  :class:`ralph.display.record_writer.RenderedRecordWriter` (the public
  writer), and
* the Status Bar lifecycle is exercised through
  ``ParallelDisplay.status_bar.start()`` / ``.stop()`` (the public
  lifecycle) against the console the test itself injected.

No private helper, private attribute, or private module path is touched.
"""

from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from ralph.display import render_status_bar
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.record_writer import RenderedRecordWriter
from ralph.display.status_bar import StatusBar, StatusBarModel


class _TtyStringIO(io.StringIO):
    """In-memory stream that reports ``isatty() is True``."""

    def isatty(self) -> bool:
        return True


def _render(phase_label: str) -> str:
    """Render the Status Bar footer for ``phase_label`` and return the plain text."""
    console = Console(file=io.StringIO(), force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={})
    model = StatusBarModel(
        workspace_root="/tmp/proj",
        phase_label=phase_label,
        phase_style="theme.phase.development",
    )
    return render_status_bar(model, ctx).plain


def test_rendered_status_bar_removes_full_terminal_control_sequences() -> None:
    """Private-parameter CSI, OSC, and two-character ESC cannot leak into labels."""
    text = "before\x1b[<35;1;2Mmouse\x1b[>0cattrs\x1b]2;title\x07title\x1bMafter"

    plain = _render(text)

    assert "\x1b" not in plain, f"rendered footer must not contain ESC bytes; got {plain!r}"
    assert "beforemouseattrstitleafter" in plain, (
        f"rendered footer must keep the visible label text; got {plain!r}"
    )


def test_rendered_status_bar_keeps_tab_normalization_behavior() -> None:
    """Tabs remain one space so width budgeting has the established behavior."""
    plain = _render("plain\tlabel")

    assert "plain label" in plain, f"tab must normalize to a single space; got {plain!r}"
    assert "\t" not in plain, f"rendered footer must not contain a tab byte; got {plain!r}"


def test_persisted_record_removes_full_terminal_control_sequences(tmp_path: Path) -> None:
    """The persisted record uses the canonical full-range terminal stripper."""
    text = "before\x1b[<35;1;2Mmouse\x1b[>0cattrs\x1b]2;title\x07title\x1bMafter"

    writer = RenderedRecordWriter(tmp_path, "agent-x")
    writer.append(
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "phase": "development",
            "cycle": "1/3",
            "iter": "2/5",
            "agent": "claude",
            "severity": "info",
            "body": text,
        }
    )
    writer.flush()

    persisted = writer.path.read_text(encoding="utf-8")
    assert "\x1b" not in persisted, (
        f"persisted record must not contain ESC bytes; got {persisted!r}"
    )
    assert "beforemouseattrstitleafter" in persisted, (
        f"persisted record must keep the visible body text; got {persisted!r}"
    )


def _build_display(buf: io.StringIO) -> tuple[ParallelDisplay, Console]:
    """Build a ``ParallelDisplay`` over the caller-owned console buffer."""
    console = Console(file=buf, force_terminal=True, width=120)
    return ParallelDisplay(make_display_context(console=console, env={})), console


def _status_bar_of(display: ParallelDisplay) -> StatusBar:
    """Return the composed Status Bar through its public accessor."""
    status_bar = display.status_bar
    assert isinstance(status_bar, StatusBar)
    return status_bar


def _model() -> StatusBarModel:
    return StatusBarModel(
        workspace_root="/tmp/proj",
        phase_label="Dev",
        phase_style="theme.phase.development",
    )


def test_status_bar_writes_no_bytes_to_non_tty_output() -> None:
    """Redirected output must never include cursor erase controls."""
    buf = io.StringIO()
    display, _console = _build_display(buf)
    status_bar = _status_bar_of(display)

    display.update_status_bar(_model())
    status_bar.start()
    status_bar.stop()

    out = buf.getvalue()
    assert out == "", f"No bytes may be written on a redirected stream; got {out!r}"
    assert "\x1b[2K" not in out, "redirected output must not contain erase-line controls"


def test_status_bar_teardown_preserves_prior_tty_transcript() -> None:
    """S-1: TTY teardown must not erase prior command output or the footer row."""
    buf = _TtyStringIO()
    display, console = _build_display(buf)
    status_bar = _status_bar_of(display)
    console.print("ralph 1.2.3 — welcome")

    display.update_status_bar(_model())
    status_bar.start()
    before_stop = buf.getvalue()
    status_bar.stop()
    after_stop = buf.getvalue()

    assert "ralph" in before_stop
    assert "welcome" in before_stop
    assert "ralph" in after_stop, "teardown must not erase prior command output"
    assert "welcome" in after_stop, "teardown must not erase prior command output"
    teardown_output = after_stop[len(before_stop) :]
    assert "\x1b[2J" not in teardown_output, "teardown must not clear the screen"
    assert "\x1b[1A" not in teardown_output, "teardown must not move the cursor up"
    assert "Dev" in teardown_output, "teardown must leave the footer row readable"


def test_status_bar_teardown_is_idempotent_and_writes_no_extra_bytes() -> None:
    """Repeated teardown calls never emit additional terminal controls."""
    buf = _TtyStringIO()
    display, _console = _build_display(buf)
    status_bar = _status_bar_of(display)

    display.update_status_bar(_model())
    status_bar.start()
    status_bar.stop()
    after_first_stop = buf.getvalue()
    status_bar.stop()

    assert buf.getvalue() == after_first_stop, "a second teardown must write nothing"
