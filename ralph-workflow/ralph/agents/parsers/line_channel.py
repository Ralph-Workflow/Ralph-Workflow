from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph._typed_attrs import attr_or_none, is_callable_obj

from .content_channel import ContentChannel, channel_of

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from .agent_output_line import AgentOutputLine


def _parser_tagged_channel(parser_key: str, raw_line: str) -> ContentChannel | None:
    from ralph.agents.parsers import get_parser

    try:
        parser = get_parser(parser_key)
    except ValueError:
        return None
    classify_line = attr_or_none(parser, "classify_line")
    if not is_callable_obj(classify_line):
        return None
    classify = cast("Callable[[str], Iterable[AgentOutputLine]]", classify_line)
    for output_line in classify(raw_line):
        return output_line.channel
    return None


def line_channel(parser_key: str | None, raw_line: str) -> ContentChannel:
    if parser_key is not None:
        tagged = _parser_tagged_channel(parser_key, raw_line)
        if tagged is not None:
            return tagged
    return channel_of(raw_line)


__all__ = ["line_channel"]
