from __future__ import annotations

import contextlib
import io

from loguru import logger
from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.log_sink import make_sanitizing_log_sink
from ralph.display.theme import RALPH_THEME


def test_sanitizing_sink_stops_writing_once_its_bound_stream_is_closed() -> None:
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

    sink("record after the stream died\n")
    sink("and another one\n")


def test_dead_bound_stream_does_not_leak_a_loguru_error_report() -> None:
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

    spilled = unrelated_stderr.getvalue()
    assert "Logging error" not in spilled
    assert "Traceback" not in spilled
