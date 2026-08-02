"""Regression coverage for semantic styling of Console-backed loguru records."""

from __future__ import annotations

from collections.abc import Iterator
from io import StringIO

import pytest
from loguru import logger

import ralph.cli.main as cli_main
from ralph.config.enums import Verbosity
from ralph.display.context import make_display_context
from ralph.display.log_sink import make_sanitizing_log_sink
from ralph.display.theme import make_console


@pytest.fixture(autouse=True)
def _reset_logger() -> Iterator[None]:
    """Keep each loguru configuration local to its assertion."""
    logger.remove()
    yield
    logger.remove()


@pytest.mark.parametrize(
    ("background", "background_is_light"),
    (("dark", False), ("light", True), ("unknown", None)),
)
def test_log_sink_regression_levels_render_with_distinct_semantic_colours(
    background: str,
    background_is_light: bool | None,
) -> None:
    """S-1: CLI log records retain per-level truecolor styling on every palette."""
    stream = StringIO()
    console = make_console(
        file=stream,
        force_terminal=True,
        terminal_bg_is_light=background_is_light,
    )
    ctx = make_display_context(
        console=console,
        env={"RALPH_TERMINAL_BG": background},
    )
    cli_main._configure_logging(Verbosity.NORMAL, console_sink=make_sanitizing_log_sink(ctx))

    logger.info("informational narration")
    logger.warning("warning narration")
    logger.error("error narration")

    rows = [row for row in stream.getvalue().splitlines() if row.strip()]
    assert all("38;2;" in row for row in rows), rows
    sgr_by_message = {
        message: row.partition(message)[0]
        for message in ("informational narration", "warning narration", "error narration")
        for row in rows
        if message in row
    }
    assert len(sgr_by_message) == 3
    assert len(set(sgr_by_message.values())) == 3
