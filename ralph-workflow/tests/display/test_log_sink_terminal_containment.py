"""Tests for the loguru log-sink terminal containment contract.

The bug: ralph/logging.py and ralph/cli/main.py both add loguru
handlers that write raw ``sys.stderr`` -- terminal-control
constructs (alternate screen ``ESC[?1049h``, erase display
``ESC[2J``, OSC titles) embedded in agent output (e.g. the raw
PTY TUI tail carried by ``AgentInactivityTimeoutError``) reach
the real terminal and blank the screen or overwrite log lines.

The fix routes BOTH logging configurators through
``ralph.display.log_sink`` -- either ``make_sanitizing_log_sink``
(uses the existing DisplayContext Console so the rich ``Live``
status bar is the only painter) or ``make_stderr_log_sink`` (a
sanitizing fallback for worker/library callers with no
DisplayContext).

These tests pin BOTH configurators. They are written RED first:
``ralph.display.log_sink`` does not exist yet, so the import fails
on the first run; that is the expected first failure. After
step 5 of the plan defines both factories, this file goes GREEN.

All tests are in-process (no subprocess, no sleep) and stay under
the per-test SIGALRM cap.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator

import pytest
from loguru import logger
from rich.console import Console

from ralph.display.context import make_display_context

HOSTILE_LINE = "\x1b[?1049h\x1b[2J\x1b]0;title\x07\x07boom"

_FORBIDDEN_BODIES = ("[?1049h", "[2J", "]0;title")


@pytest.fixture(autouse=True)
def _reset_logger() -> Iterator[None]:
    """Drop any handler the test installed so it cannot leak into siblings."""
    logger.remove()
    yield
    logger.remove()


def _stringio_console(*, width: int = 120) -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=False,
        color_system=None,
        width=width,
    )
    return console, buf


def _assert_no_escape_leak(output: str, *, sink_label: str) -> None:
    assert "\x1b" not in output, (
        f"{sink_label}: bare ESC byte leaked into log sink output: {output!r}"
    )
    for forbidden in _FORBIDDEN_BODIES:
        assert forbidden not in output, (
            f"{sink_label}: hostile body {forbidden!r} leaked through sink; "
            f"output={output!r}"
        )


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def test_sink_strips_terminal_control() -> None:
    """``make_sanitizing_log_sink`` strips every terminal-control construct.

    Drives the Console-backed sink with the hostile line combining
    alternate-screen, erase-display, OSC title, and bare C0 BEL. The
    visible text ``boom`` must survive; no ``\\x1b`` byte and no
    body residue must leak through.
    """
    from ralph.display.log_sink import make_sanitizing_log_sink

    console, buf = _stringio_console()
    ctx = make_display_context(console=console, env={"CI": "1"})
    sink: Callable[[str], None] = make_sanitizing_log_sink(ctx)

    sink(HOSTILE_LINE)

    output = buf.getvalue()
    assert "boom" in output, f"visible 'boom' must survive; got {output!r}"
    _assert_no_escape_leak(output, sink_label="sanitizing_sink")


def test_sink_writes_through_display_console_not_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Console-backed sink does NOT write raw to ``sys.stderr``.

    Anti-double-painter assertion: the sanitizing sink must route
    through the injected Console; ``sys.stderr`` stays empty so the
    rich ``Live`` status bar is the only painter of the terminal.
    """
    from ralph.display.log_sink import make_sanitizing_log_sink

    console, buf = _stringio_console()
    ctx = make_display_context(console=console, env={"CI": "1"})

    stderr_recorded = io.StringIO()
    monkeypatch.setattr("sys.stderr", stderr_recorded)

    sink = make_sanitizing_log_sink(ctx)
    sink("payload: \x1b[?1049h\x1b[2Jboom")

    console_output = buf.getvalue()
    stderr_output = stderr_recorded.getvalue()

    assert "boom" in console_output, (
        f"the visible 'boom' must survive; got console_output={console_output!r}"
    )
    _assert_no_escape_leak(console_output, sink_label="sanitizing_sink (console)")
    assert stderr_output == "", (
        f"sanitizing sink must not write raw to sys.stderr; got stderr={stderr_output!r}"
    )


def test_sink_does_not_reinterpret_rich_markup() -> None:
    """Bracketed text and rich tokens survive verbatim (markup=False, highlight=False)."""
    from ralph.display.log_sink import make_sanitizing_log_sink

    console, buf = _stringio_console()
    ctx = make_display_context(console=console, env={"CI": "1"})
    sink = make_sanitizing_log_sink(ctx)

    sink("[bold]some path: /tmp/file.txt\n")

    output = buf.getvalue()
    assert "[bold]" in output
    assert "/tmp/file.txt" in output


def test_default_sink_still_sanitizes_without_a_display_context() -> None:
    """``make_stderr_log_sink`` strips escapes even when no Console is around.

    Fallback path for worker / library callers that have no DisplayContext.
    Sanitization is still mandatory -- only the rendering surface changes.
    """
    from ralph.display.log_sink import make_stderr_log_sink

    buf = io.StringIO()
    sink = make_stderr_log_sink(writer=buf.write)

    sink("payload: \x1b[?1049h\x1b[2Jboom")

    output = buf.getvalue()
    assert "boom" in output, f"visible 'boom' must survive; got {output!r}"
    _assert_no_escape_leak(output, sink_label="stderr_sink")


def test_stderr_sink_does_not_reinterpret_rich_markup() -> None:
    """``make_stderr_log_sink`` preserves bracketed tokens verbatim.

    Same rationale as the Console-backed variant: loguru records may
    contain ``[bold]`` or bracketed paths that must NOT be re-evaluated as
    rich markup (the writing surface is plain text via ``buf.write``).
    """
    from ralph.display.log_sink import make_stderr_log_sink

    buf = io.StringIO()
    sink = make_stderr_log_sink(writer=buf.write)
    sink("[bold]/tmp/file.txt\n")

    output = buf.getvalue()
    assert "[bold]" in output
    assert "/tmp/file.txt" in output


# ---------------------------------------------------------------------------
# Library configurator (ralph.logging.configure_logging)
# ---------------------------------------------------------------------------


def test_library_configure_logging_installs_no_stderr_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ralph.logging.configure_logging`` writes through the injected sink, not stderr.

    The library configurator is the public API exported in
    ``ralph.logging.__all__`` and imported by
    ``tests/unit/test_logging_buffering.py`` / ``tests/test_logging_extensions.py``.
    It must accept a ``console_sink`` keyword and route through it -- never
    raw ``sys.stderr`` -- so a repository-wide grep for
    ``logger.add(sys.stderr`` returns no output from this file.
    """
    from ralph.display.log_sink import make_stderr_log_sink
    from ralph.logging import configure_logging

    stderr_recorded = io.StringIO()
    monkeypatch.setattr("sys.stderr", stderr_recorded)

    captured: list[str] = []

    def recorder(message: str) -> None:
        captured.append(message)

    sink = make_stderr_log_sink(writer=recorder)
    session = configure_logging(verbosity=2, console_sink=sink)
    logger.bind(recorder_invocation=True).info("tail: \x1b[2Jblack")

    try:
        joined = "\n".join(captured)
        assert any("tail:" in record for record in captured), (
            f"the recorder must have received at least one record; got {captured!r}"
        )
        assert "\x1b" not in joined, (
            f"the recorded text must be sanitized; got {joined!r}"
        )
        assert stderr_recorded.getvalue() == "", (
            f"library configurator must not write raw to sys.stderr; "
            f"got stderr={stderr_recorded.getvalue()!r}"
        )
    finally:
        # ``configure_logging`` may register multiple handlers (terminal +
        # per-worker); remove them so other tests are not affected.
        logger.remove()
        # Touch the session object so linters do not flag it as unused; the
        # returned value is documented as a LoggingSession with paths/binding.
        del session


# ---------------------------------------------------------------------------
# CLI configurator (ralph.cli.main._configure_logging)
# ---------------------------------------------------------------------------


def _exercise_cli_configure_logging(
    monkeypatch: pytest.MonkeyPatch,
    verbosity: str,
) -> tuple[io.StringIO, list[str]]:
    """Invoke the CLI's ``_configure_logging`` and capture both stderr and the sink."""
    from ralph.cli.main import _configure_logging
    from ralph.config.enums import Verbosity
    from ralph.display.log_sink import make_stderr_log_sink

    stderr_recorded = io.StringIO()
    monkeypatch.setattr("sys.stderr", stderr_recorded)
    captured: list[str] = []

    sink = make_stderr_log_sink(writer=captured.append)
    _configure_logging(Verbosity(verbosity), console_sink=sink)

    logger.bind(cli_configure_invocation=True).info(
        "through_cli: \x1b[?1049h\x1b[2J\x1b]0;title\x07boom"
    )

    return stderr_recorded, captured


def test_cli_configure_logging_installs_no_stderr_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ralph.cli.main._configure_logging`` is the sink the CLI actually runs.

    The previous plan only fixed ``ralph.logging.configure_logging`` --
    the rebind at ``ralph/cli/main.py:1357`` makes the CLI use the
    LOCAL ``_configure_logging`` instead, so that fix missed the real
    black-screen path. This test pins it across multiple verbosity
    branches.
    """
    stderr_recorded, captured = _exercise_cli_configure_logging(monkeypatch, "normal")

    stderr_output = stderr_recorded.getvalue()
    joined = "\n".join(captured)

    assert any("through_cli:" in record for record in captured), (
        f"the CLI sink must have received the record; got {captured!r}"
    )
    assert "\x1b" not in joined, (
        f"CLI records must be sanitized; got {joined!r}"
    )
    assert stderr_output == "", (
        f"_configure_logging must not write raw to sys.stderr; got {stderr_output!r}"
    )


@pytest.mark.parametrize("verbosity", ["quiet", "normal", "verbose", "full", "debug"])
def test_cli_configure_logging_no_stderr_handler_for_every_branch(
    monkeypatch: pytest.MonkeyPatch,
    verbosity: str,
) -> None:
    """Every verbosity branch routes through the injected sink.

    Covers the 5 branches at ``ralph/cli/main.py:1282-1290`` so a
    regression in any single branch (one raw ``sys.stderr``
    re-introduced) fails the gate.
    """
    stderr_recorded, captured = _exercise_cli_configure_logging(monkeypatch, verbosity)

    stderr_output = stderr_recorded.getvalue()
    joined = "\n".join(captured)

    assert stderr_output == "", (
        f"_configure_logging({verbosity}) must not write raw to sys.stderr; "
        f"got stderr={stderr_output!r}"
    )
    assert "\x1b" not in joined, (
        f"_configure_logging({verbosity}) records must be sanitized; got {joined!r}"
    )


def test_no_call_site_hands_sys_stderr_to_logger_add() -> None:
    """Repo-wide grep: ``logger.add(sys.stderr`` must appear nowhere in ralph/.

    Final defence-in-depth: a static check that both
    ``ralph.logging.configure_logging`` and
    ``ralph.cli.main._configure_logging`` stay routed through the
    sanitizing sink forever.
    """
    import os
    import re
    from pathlib import Path

    ralph_root = Path(__file__).resolve().parent.parent.parent / "ralph"
    offenders: list[str] = []
    needle = re.compile(r"logger\.add\(\s*sys\.stderr")
    for directory, _dirs, names in os.walk(ralph_root):
        for name in names:
            if not name.endswith(".py"):
                continue
            path = Path(directory, name)
            source = path.read_text(encoding="utf-8")
            if "logger.add" not in source or "sys.stderr" not in source:
                continue
            for lineno, line in enumerate(source.splitlines(), start=1):
                if needle.search(line):
                    offenders.append(f"{path}:{lineno}: {line.strip()}")
    assert not offenders, (
        "logger.add(sys.stderr, ...) re-introduced; must route through "
        "ralph.display.log_sink:\n" + "\n".join(offenders)
    )
