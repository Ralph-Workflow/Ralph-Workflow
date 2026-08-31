"""A console log sink must not outlive the stream it was bound to.

``make_sanitizing_log_sink`` binds one ``Console`` -- and therefore one
file object -- for the life of the sink, while the loguru handler that
holds it lives until something calls ``logger.remove()``. When the file
is closed first (a CLI invocation ends, ``ralph run | head`` closes the
pipe), every later record used to raise out of loguru's ``emit``; loguru
then printed ``--- Logging error in Loguru Handler #N ---`` plus a full
Python traceback to whatever ``sys.stderr`` was current, corrupting an
unrelated consumer's output.

Pin the contract: once the bound surface is dead the sink goes quiet.
"""

from __future__ import annotations

import contextlib
import io

from loguru import logger
from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.log_sink import make_sanitizing_log_sink
from ralph.display.theme import RALPH_THEME


def test_sanitizing_sink_stops_writing_once_its_bound_stream_is_closed() -> None:
    """A closed bound stream retires the sink instead of raising."""
    stream = io.StringIO()
    sink = make_sanitizing_log_sink(
        make_display_context(
            console=Console(file=stream, force_terminal=False, width=80, theme=RALPH_THEME),
            env={},
        )
    )

    sink("live record\n")
    assert "live record" in stream.getvalue()

    stream.close()

    # Must not raise: the surface is gone, so the record is dropped.
    sink("record after the stream died\n")
    sink("and another one\n")


def test_dead_bound_stream_does_not_leak_a_loguru_error_report() -> None:
    """A retired sink emits no loguru error block to the current stderr."""
    bound = io.StringIO()
    sink = make_sanitizing_log_sink(
        make_display_context(
            console=Console(file=bound, force_terminal=False, width=80, theme=RALPH_THEME),
            env={},
        )
    )
    bound.close()

    unrelated_stderr = io.StringIO()
    handler_id = logger.add(sink, level="DEBUG", format="{message}")
    try:
        with contextlib.redirect_stderr(unrelated_stderr):
            logger.error("a record nobody can write")
    finally:
        logger.remove(handler_id)

    # Only assert on the leak itself: another handler registered by an
    # unrelated test may legitimately resolve ``sys.stderr`` at call
    # time and write the record's own text here.
    spilled = unrelated_stderr.getvalue()
    assert "Logging error" not in spilled
    assert "Traceback" not in spilled
