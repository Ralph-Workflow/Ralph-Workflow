from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

from .content_channel import ContentChannel, channel_of

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from .agent_output_line import AgentOutputLine


def _parser_tagged_channel(parser_key: str, raw_line: str) -> ContentChannel | None:
    parsers_pkg = import_module("ralph.agents.parsers")
    get_parser = cast("Callable[[str], object] | None", getattr(parsers_pkg, "get_parser", None))
    if get_parser is None:
        return None
    try:
        parser = get_parser(parser_key)
    except ValueError:
        return None
    classify_line = cast(
        "Callable[[str], Iterator[AgentOutputLine]] | None",
        getattr(parser, "classify_line", None),
    )
    if classify_line is None:
        return None
    for output_line in classify_line(raw_line):
        return output_line.channel
    return None


def line_channel(parser_key: str | None, raw_line: str) -> ContentChannel:
    if parser_key is not None:
        tagged = _parser_tagged_channel(parser_key, raw_line)
        if tagged is not None:
            return tagged
    return channel_of(raw_line)


__all__ = ["line_channel"]
